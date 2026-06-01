import os
import cv2
import json
import base64
import shutil
import logging
import numpy as np
from datetime import datetime
from fastapi import FastAPI, Depends, Request, Form, UploadFile, File, HTTPException, status
from fastapi.responses import HTMLResponse, JSONResponse, FileResponse, RedirectResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from sqlalchemy.orm import Session
import folium

from .database import engine, get_db, Base, SessionLocal
from .models import ImageRecord, LineRecord, OptimizationConfig
from .core import extract_exif_metadata, extraer_perfil_banda, calcular_distancia_fourier, detectar_picos, safe_imread, process_gray_conversion, apply_pre_filter

logger = logging.getLogger("main")

# Create the DB tables on launch
Base.metadata.create_all(bind=engine)

app = FastAPI(title="Log Counting System - Conteo de Rollizos")

# Directory setups
UPLOAD_DIR = os.getenv("UPLOAD_DIR", "uploads")
os.makedirs(UPLOAD_DIR, exist_ok=True)

# Mount static and uploads
app.mount("/static", StaticFiles(directory="app/static"), name="static")
app.mount("/uploads", StaticFiles(directory=UPLOAD_DIR), name="uploads")

templates = Jinja2Templates(directory="app/templates")

@app.on_event("startup")
def seed_database():
    """
    Startup event to copy the default example image, seed it in the database with
    pre-calculated coordinates, and purge broken local database entries.
    """
    db = SessionLocal()
    try:
        # 1. Copy the default example image to uploads folder if not exists
        static_example = "app/static/example_image.jpg"
        uploads_example = os.path.join(UPLOAD_DIR, "example_image.jpg")
        if os.path.exists(static_example):
            if not os.path.exists(uploads_example):
                logger.info("Copying static example image to uploads directory...")
                shutil.copy(static_example, uploads_example)
        
        # 2. Purge broken database entries pointing to non-existent files (e.g. local paths on Render)
        images = db.query(ImageRecord).all()
        for img in images:
            if not os.path.exists(img.filepath):
                logger.info(f"Removing broken legacy image record: {img.filename} (file not found)")
                db.delete(img)
        db.commit()
        
        # 3. Seed example_image.jpg if it doesn't exist in the database
        example_record = db.query(ImageRecord).filter(ImageRecord.filename == "example_image.jpg").first()
        if not example_record and os.path.exists(uploads_example):
            logger.info("Seeding default example image in database...")
            example_record = ImageRecord(
                filename="example_image.jpg",
                filepath=uploads_example,
                gps_lat=-34.8152,
                gps_lon=-58.4619,
                gps_alt=100.0,
                status="Optimized",
                total_detected=49,
                total_gt=51
            )
            db.add(example_record)
            db.commit()
            db.refresh(example_record)
            
            # Seed reference line coordinates for example_image.jpg
            line_record = LineRecord(
                image_id=example_record.id,
                p1_x=293.4353,
                p1_y=346.5547,
                p2_x=1415.6653,
                p2_y=470.7497,
                ground_truth=51,
                detected_count=49
            )
            db.add(line_record)
            
            # Seed active configuration parameters so detection doesn't fail
            config_record = OptimizationConfig(
                image_id=example_record.id,
                gray_conversion="LAB L Channel",
                profile_type="Band Averaged",
                band_width=30,
                pre_filter="Bilateral Filter",
                distance_mode="Adaptive Fourier",
                detection_method="direct_peaks",
                wape=0.039,
                mae=2.0
            )
            db.add(config_record)
            db.commit()
            logger.info("Example image and lines seeded successfully!")
            
    except Exception as e:
        db.rollback()
        logger.error(f"Startup seeding failed: {e}")
    finally:
        db.close()

@app.get("/", response_class=HTMLResponse)
def dashboard(request: Request, db: Session = Depends(get_db)):
    """
    Renders the central image dashboard.
    Fetches all uploaded images, compiles metrics, and loads GPS geolocations on a Folium map.
    """
    images = db.query(ImageRecord).order_by(ImageRecord.upload_date.desc()).all()
    
    # Calculate flight map coordinates
    map_center = [-34.8152, -58.4619]  # Default Argentina / LatAm center fallback
    coordinates_list = []
    
    for img in images:
        if img.gps_lat is not None and img.gps_lon is not None:
            coordinates_list.append([img.gps_lat, img.gps_lon, img.filename, img.id, img.status])
            
    # Re-center map based on actual coordinates if available
    if coordinates_list:
        map_center = [
            sum(c[0] for c in coordinates_list) / len(coordinates_list),
            sum(c[1] for c in coordinates_list) / len(coordinates_list)
        ]
        
    # Create Folium Map with premium CartoDB dark theme
    f_map = folium.Map(
        location=map_center,
        zoom_start=13 if coordinates_list else 4,
        tiles="CartoDB dark_matter",
        control_scale=True
    )
    
    # Add image location markers
    for lat, lon, fname, img_id, status_badge in coordinates_list:
        color = "red"
        if status_badge == "Optimized":
            color = "green"
        elif status_badge == "Report Generated":
            color = "purple"
            
        popup_html = f"""
        <div style="font-family: 'Inter', sans-serif; color: #1e293b; padding: 4px;">
            <h4 style="margin: 0 0 6px 0; font-size: 14px; font-weight: 600;">{fname}</h4>
            <p style="margin: 0 0 8px 0; font-size: 11px; color: #64748b;">Status: <strong>{status_badge}</strong></p>
            <a href="/image/{img_id}" target="_parent" style="display: inline-block; background-color: #10b981; color: white; padding: 4px 8px; border-radius: 4px; font-size: 11px; text-decoration: none; font-weight: 500;">Open Visor</a>
        </div>
        """
        
        folium.Marker(
            location=[lat, lon],
            popup=folium.Popup(popup_html, max_width=300),
            tooltip=fname,
            icon=folium.Icon(color=color, icon="plane", prefix="fa")
        ).add_to(f_map)
        
    map_html = f_map._repr_html_()
    
    # Calculate stats
    total_images = len(images)
    total_detected = sum(img.total_detected for img in images)
    total_gt = sum(img.total_gt for img in images)
    global_error_pct = 0.0
    if total_gt > 0:
        global_error_pct = (abs(total_detected - total_gt) / total_gt) * 100
        
    return templates.TemplateResponse(
        request=request,
        name="dashboard.html",
        context={
            "images": images,
            "map_html": map_html,
            "stats": {
                "total_images": total_images,
                "total_detected": total_detected,
                "total_gt": total_gt,
                "global_error_pct": round(global_error_pct, 1)
            }
        }
    )

@app.post("/upload")
async def upload_image(file: UploadFile = File(...), db: Session = Depends(get_db)):
    """
    Endpoint for uploading raw drone orthomosaics.
    Extracts EXIF metadata coordinates and saves the file locally.
    """
    if not file.filename:
        raise HTTPException(status_code=400, detail="Empty filename uploaded")
        
    # Save the file with timestamps to prevent naming collisions
    timestamp = datetime.now().strftime("%Y%m%d%H%M%S")
    clean_filename = f"{timestamp}_{file.filename}"
    filepath = os.path.join(UPLOAD_DIR, clean_filename)
    
    try:
        content = await file.read()
        with open(filepath, "wb") as f:
            f.write(content)
            
        # Parse GPS coordinates
        meta = extract_exif_metadata(filepath)
        
        # Save DB image entry with the actual saved filename
        db_image = ImageRecord(
            filename=clean_filename,
            filepath=filepath,
            gps_lat=meta["gps_lat"],
            gps_lon=meta["gps_lon"],
            gps_alt=meta["gps_alt"],
            status="Pending Lines"
        )
        db.add(db_image)
        db.commit()
        db.refresh(db_image)
        
        return RedirectResponse(url=f"/image/{db_image.id}", status_code=status.HTTP_303_SEE_OTHER)
    except Exception as e:
        logger.error(f"Image upload failed: {e}")
        raise HTTPException(status_code=500, detail=f"Image upload failed: {str(e)}")

@app.get("/image/{image_id}", response_class=HTMLResponse)
def visor(image_id: int, request: Request, db: Session = Depends(get_db)):
    """
    Renders the main visor dashboard workspace for drawing reference lines,
    visualizing intensity graphs, and triggering optimizations.
    """
    img_record = db.query(ImageRecord).filter(ImageRecord.id == image_id).first()
    if not img_record:
        raise HTTPException(status_code=404, detail="Image not found")
        
    lines = db.query(LineRecord).filter(LineRecord.image_id == image_id).all()
    opt_config = db.query(OptimizationConfig).filter(OptimizationConfig.image_id == image_id).first()
    
    # Derive the correct visual filename from filepath to support all legacy database entries
    image_filename = os.path.basename(img_record.filepath)
    image_url = f"/uploads/{image_filename}"
    
    return templates.TemplateResponse(
        request=request,
        name="visor.html",
        context={
            "image": img_record,
            "image_url": image_url,
            "lines": lines,
            "config": opt_config
        }
    )

@app.get("/api/image/{image_id}/lines")
def get_lines(image_id: int, db: Session = Depends(get_db)):
    """
    Returns drawn lines coordinates list in JSON.
    """
    lines = db.query(LineRecord).filter(LineRecord.image_id == image_id).all()
    return [{
        "id": l.id,
        "p1_x": l.p1_x,
        "p1_y": l.p1_y,
        "p2_x": l.p2_x,
        "p2_y": l.p2_y,
        "ground_truth": l.ground_truth,
        "detected_count": l.detected_count
    } for l in lines]

@app.post("/api/image/{image_id}/lines")
async def save_lines(image_id: int, request: Request, db: Session = Depends(get_db)):
    """
    Saves drawn lines and their respective user-defined ground truth counts.
    """
    img_record = db.query(ImageRecord).filter(ImageRecord.id == image_id).first()
    if not img_record:
        raise HTTPException(status_code=404, detail="Image not found")
        
    try:
        body = await request.json()
        lines_data = body.get("lines", [])
        
        # Wipe old line entries
        db.query(LineRecord).filter(LineRecord.image_id == image_id).delete()
        
        total_gt = 0
        for item in lines_data:
            line_db = LineRecord(
                image_id=image_id,
                p1_x=float(item["p1_x"]),
                p1_y=float(item["p1_y"]),
                p2_x=float(item["p2_x"]),
                p2_y=float(item["p2_y"]),
                ground_truth=int(item["ground_truth"]),
                detected_count=0
            )
            db.add(line_db)
            total_gt += int(item["ground_truth"])
            
        img_record.total_gt = total_gt
        # If we added lines, update state back from Optimized/Report if they modified references
        if img_record.status == "Report Generated":
            img_record.status = "Optimized"
            
        db.commit()
        
        # Query saved lines to return their fresh database IDs
        saved_lines = db.query(LineRecord).filter(LineRecord.image_id == image_id).all()
        lines_response = [{
            "id": l.id,
            "image_id": l.image_id,
            "p1_x": l.p1_x,
            "p1_y": l.p1_y,
            "p2_x": l.p2_x,
            "p2_y": l.p2_y,
            "ground_truth": l.ground_truth,
            "detected_count": l.detected_count
        } for l in saved_lines]
        
        return {
            "success": True, 
            "message": "Lines saved successfully",
            "lines": lines_response
        }
    except Exception as e:
        db.rollback()
        logger.error(f"Failed to save lines: {e}")
        return JSONResponse(status_code=400, content={"success": False, "detail": str(e)})

# Optimization endpoint removed to simplify codebase and prevent Render CPU timeouts

@app.post("/api/image/{image_id}/run_detection")
async def run_detection(
    image_id: int,
    request: Request,
    db: Session = Depends(get_db)
):
    """
    Executes peak-detection algorithm using specified (or saved optimal) parameters.
    Saves count parameters and returns peaks pixel coordinates for canvas rendering.
    """
    img_record = db.query(ImageRecord).filter(ImageRecord.id == image_id).first()
    if not img_record:
        raise HTTPException(status_code=404, detail="Image not found")
        
    lines = db.query(LineRecord).filter(LineRecord.image_id == image_id).all()
    if not lines:
         return {"success": True, "detected_lines": [], "total_detected": 0}
         
    # Parse inputs (could be dynamic override or fallback to DB config)
    body = await request.json()
    
    opt_cfg = db.query(OptimizationConfig).filter(OptimizationConfig.image_id == image_id).first()
    
    gray_conversion = body.get("gray_conversion", opt_cfg.gray_conversion if opt_cfg else "LAB L Channel")
    pre_filter = body.get("pre_filter", opt_cfg.pre_filter if opt_cfg else "Bilateral Filter")
    profile_type = body.get("profile_type", opt_cfg.profile_type if opt_cfg else "Band Averaged")
    band_width = int(body.get("band_width", opt_cfg.band_width if opt_cfg else 30))
    distance_mode = body.get("distance_mode", opt_cfg.distance_mode if opt_cfg else "Adaptive Fourier")
    detection_method = body.get("detection_method", opt_cfg.detection_method if opt_cfg else "direct_peaks")
    
    img_bgr = safe_imread(img_record.filepath)
    if img_bgr is None:
        raise HTTPException(status_code=500, detail="Failed to load image file")
        
    # Pre-process image based on conversion and filter parameters
    gray = process_gray_conversion(img_bgr, gray_conversion)
    filtered = apply_pre_filter(gray, pre_filter)
    
    total_detected = 0
    detected_lines_response = []
    
    for l in lines:
        p1 = (l.p1_x, l.p1_y)
        p2 = (l.p2_x, l.p2_y)
        
        # 1. Profile extraction
        h = band_width if profile_type == "Band Averaged" else 1
        profile = extraer_perfil_banda(filtered, p1, p2, h)
        
        # 2. Distance mode
        if distance_mode == "Adaptive Fourier":
            min_dist = calcular_distancia_fourier(profile)
        else:
            min_dist = 20.0
            
        # 3. Detection
        peak_indices, peak_coords, _, _ = detectar_picos(profile, p1, p2, detection_method, min_dist)
        
        # Update line metrics in DB
        detected_count = len(peak_indices)
        l.detected_count = detected_count
        total_detected += detected_count
        
        detected_lines_response.append({
            "line_id": l.id,
            "detected_count": detected_count,
            "peaks": peak_coords,  # Coordinates [[x, y], ...]
            "adaptive_dist": min_dist
        })
        
    # Update image record metrics
    img_record.total_detected = total_detected
    
    # Save the parameters used as the active configuration
    if not opt_cfg:
        opt_cfg = OptimizationConfig(image_id=image_id)
        db.add(opt_cfg)
        
    opt_cfg.gray_conversion = gray_conversion
    opt_cfg.pre_filter = pre_filter
    opt_cfg.profile_type = profile_type
    opt_cfg.band_width = band_width
    opt_cfg.distance_mode = distance_mode
    opt_cfg.detection_method = detection_method
    
    # If parameters match optimal DB parameters, keep "Optimized", otherwise keep it
    if img_record.status == "Pending Lines":
        img_record.status = "Optimized"
        
    db.commit()
    
    return {
        "success": True,
        "detected_lines": detected_lines_response,
        "total_detected": total_detected
    }

@app.get("/api/image/{image_id}/line/{line_id}/profile")
def get_line_profile(
    image_id: int,
    line_id: int,
    gray_conversion: str = None,
    pre_filter: str = None,
    profile_type: str = None,
    band_width: int = None,
    distance_mode: str = None,
    detection_method: str = None,
    db: Session = Depends(get_db)
):
    """
    Extracts raw vs processed signal profiles for a selected line.
    Returns: x indices, raw intensity list, processed profile list, and detected peak indices.
    """
    img_record = db.query(ImageRecord).filter(ImageRecord.id == image_id).first()
    l = db.query(LineRecord).filter(LineRecord.id == line_id).first()
    
    if not img_record or not l:
        raise HTTPException(status_code=404, detail="Record not found")
        
    opt_cfg = db.query(OptimizationConfig).filter(OptimizationConfig.image_id == image_id).first()
    
    # Fallback cascade to active configurations
    gc = gray_conversion or (opt_cfg.gray_conversion if opt_cfg else "LAB L Channel")
    pf = pre_filter or (opt_cfg.pre_filter if opt_cfg else "Bilateral Filter")
    pt = profile_type or (opt_cfg.profile_type if opt_cfg else "Band Averaged")
    bw = int(band_width) if band_width is not None else (opt_cfg.band_width if opt_cfg else 30)
    dm = distance_mode or (opt_cfg.distance_mode if opt_cfg else "Adaptive Fourier")
    dm_method = detection_method or (opt_cfg.detection_method if opt_cfg else "direct_peaks")
    
    img_bgr = safe_imread(img_record.filepath)
    if img_bgr is None:
        raise HTTPException(status_code=500, detail="Failed to load image file")
        
    p1 = (l.p1_x, l.p1_y)
    p2 = (l.p2_x, l.p2_y)
    
    # Extract raw intensity (Standard gray without filters)
    raw_gray = process_gray_conversion(img_bgr, "Standard Gray")
    raw_profile = extraer_perfil_banda(raw_gray, p1, p2, h=1)
    
    # Extract processed intensity profile
    gray = process_gray_conversion(img_bgr, gc)
    filtered = apply_pre_filter(gray, pf)
    
    h = bw if pt == "Band Averaged" else 1
    processed_profile = extraer_perfil_banda(filtered, p1, p2, h)
    
    # Compute peak distance
    if dm == "Adaptive Fourier":
        min_dist = calcular_distancia_fourier(processed_profile)
    else:
        min_dist = 20.0
        
    # Detections
    peak_indices, _, norm_perfil, _ = detectar_picos(processed_profile, p1, p2, dm_method, min_dist)
    
    # Package response
    return {
        "x": list(range(len(raw_profile))),
        "raw_profile": raw_profile.tolist(),
        "processed_profile": processed_profile.tolist(),
        "normalized_profile": norm_perfil.tolist(),
        "peaks": peak_indices.tolist(),
        "min_dist": min_dist
    }

@app.get("/image/{image_id}/report")
def export_html_report(image_id: int, db: Session = Depends(get_db)):
    """
    Generates a single-file, self-contained, downloadable HTML Report.
    Embeds the flight orthomosaic image in Base64 and interactive summary plots.
    """
    img_record = db.query(ImageRecord).filter(ImageRecord.id == image_id).first()
    if not img_record:
        raise HTTPException(status_code=404, detail="Image not found")
        
    lines = db.query(LineRecord).filter(LineRecord.image_id == image_id).all()
    opt_cfg = db.query(OptimizationConfig).filter(OptimizationConfig.image_id == image_id).first()
    
    # Update state
    img_record.status = "Report Generated"
    db.commit()
    
    # 1. Base64 encode the orthomosaic thumbnail for offline rendering (resize if too massive for DOM)
    img_bgr = safe_imread(img_record.filepath)
    if img_bgr is None:
        raise HTTPException(status_code=500, detail="Cannot load file for report")
        
    # Downscale image to max 1200px width to keep base64 report sizes reasonable (under 5MB)
    h, w = img_bgr.shape[:2]
    max_dim = 1000
    if max(h, w) > max_dim:
        scale = max_dim / max(h, w)
        img_resized = cv2.resize(img_bgr, (int(w * scale), int(h * scale)))
    else:
        img_resized = img_bgr
        scale = 1.0
        
    _, buffer = cv2.imencode(".jpg", img_resized, [cv2.IMWRITE_JPEG_QUALITY, 80])
    img_base64 = base64.b64encode(buffer).decode("utf-8")
    
    # Pre-render detection markers on the resized image base64
    marked_img = img_resized.copy()
    
    lines_summary = []
    
    # Re-run detection to gather peak coordinates for drawing overlay
    if opt_cfg:
        gray = process_gray_conversion(img_bgr, opt_cfg.gray_conversion)
        filtered = apply_pre_filter(gray, opt_cfg.pre_filter)
        h_band = opt_cfg.band_width if opt_cfg.profile_type == "Band Averaged" else 1
        
        for l in lines:
            p1 = (l.p1_x, l.p1_y)
            p2 = (l.p2_x, l.p2_y)
            
            profile = extraer_perfil_banda(filtered, p1, p2, h_band)
            dist_val = calcular_distancia_fourier(profile) if opt_cfg.distance_mode == "Adaptive Fourier" else 20.0
            peak_indices, peak_coords, _, _ = detectar_picos(profile, p1, p2, opt_cfg.detection_method, dist_val)
            
            # Draw on marked_img
            # Rescale drawn coordinates to match resized dimensions
            p1_scaled = (int(l.p1_x * scale), int(l.p1_y * scale))
            p2_scaled = (int(l.p2_x * scale), int(l.p2_y * scale))
            
            # Draw line
            cv2.line(marked_img, p1_scaled, p2_scaled, (0, 0, 255), 2)
            
            # Draw circular peaks
            for pc in peak_coords:
                cx, cy = int(pc[0] * scale), int(pc[1] * scale)
                cv2.circle(marked_img, (cx, cy), 5, (0, 0, 0), -1) # Black outline
                cv2.circle(marked_img, (cx, cy), 4, (0, 255, 0), -1) # Green center
                
            lines_summary.append({
                "id": l.id,
                "p1": [int(l.p1_x), int(l.p1_y)],
                "p2": [int(l.p2_x), int(l.p2_y)],
                "gt": l.ground_truth,
                "det": len(peak_coords)
            })
            
    _, marked_buffer = cv2.imencode(".jpg", marked_img, [cv2.IMWRITE_JPEG_QUALITY, 80])
    marked_base64 = base64.b64encode(marked_buffer).decode("utf-8")
    
    # 2. Compile standalone HTML templates
    total_det = sum(l["det"] for l in lines_summary)
    total_gt = sum(l["gt"] for l in lines_summary)
    mae = np.mean([abs(l["det"] - l["gt"]) for l in lines_summary]) if lines_summary else 0.0
    wape = sum(abs(l["det"] - l["gt"]) for l in lines_summary) / total_gt if total_gt > 0 else 0.0
    
    html_content = f"""<!DOCTYPE html>
<html lang="en">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <title>Conteo de Rollizos - Standalone Report</title>
    <script src="https://cdn.tailwindcss.com"></script>
    <script src="https://cdn.jsdelivr.net/npm/apexcharts"></script>
    <link href="https://fonts.googleapis.com/css2?family=Outfit:wght@300;400;500;600;700&display=swap" rel="stylesheet">
    <style>
        body {{
            font-family: 'Outfit', sans-serif;
            background-color: #0f172a;
            color: #f8fafc;
        }}
    </style>
</head>
<body class="p-8 max-w-6xl mx-auto">
    <header class="flex justify-between items-center border-b border-slate-800 pb-6 mb-8">
        <div>
            <h1 class="text-3xl font-bold text-emerald-400">Conteo de Rollizos (Logs)</h1>
            <p class="text-slate-400 text-sm mt-1">Automatic counting report generated on {datetime.now().strftime("%Y-%m-%d %H:%M")}</p>
        </div>
        <div class="text-right">
            <span class="bg-emerald-950 border border-emerald-500 text-emerald-400 font-semibold px-4 py-2 rounded-lg text-sm shadow">
                File: {img_record.filename}
            </span>
        </div>
    </header>

    <div class="grid grid-cols-1 md:grid-cols-4 gap-6 mb-8">
        <div class="bg-slate-900 border border-slate-800 p-5 rounded-2xl">
            <p class="text-xs text-slate-400 font-semibold uppercase tracking-wider">Total Detected</p>
            <p class="text-4xl font-extrabold text-white mt-2">{total_det}</p>
        </div>
        <div class="bg-slate-900 border border-slate-800 p-5 rounded-2xl">
            <p class="text-xs text-slate-400 font-semibold uppercase tracking-wider">Ground Truth Total</p>
            <p class="text-4xl font-extrabold text-white mt-2">{total_gt}</p>
        </div>
        <div class="bg-slate-900 border border-slate-800 p-5 rounded-2xl">
            <p class="text-xs text-slate-400 font-semibold uppercase tracking-wider">Mean Absolute Error (MAE)</p>
            <p class="text-4xl font-extrabold text-amber-400 mt-2">{mae:.2f}</p>
        </div>
        <div class="bg-slate-900 border border-slate-800 p-5 rounded-2xl">
            <p class="text-xs text-slate-400 font-semibold uppercase tracking-wider">WAPE Accuracy</p>
            <p class="text-4xl font-extrabold text-emerald-400 mt-2">{(1.0 - wape)*100:.1f}%</p>
        </div>
    </div>

    <div class="grid grid-cols-1 lg:grid-cols-3 gap-8 mb-8">
        <div class="lg:col-span-2 bg-slate-900 border border-slate-800 rounded-3xl p-6 shadow-xl">
            <h3 class="text-lg font-bold text-white mb-4">Detected Logs Overlaid Workspace</h3>
            <div class="relative overflow-hidden rounded-xl border border-slate-800">
                <img src="data:image/jpeg;base64,{marked_base64}" class="w-full h-auto object-contain" alt="Overlay detections">
            </div>
        </div>

        <div class="bg-slate-900 border border-slate-800 rounded-3xl p-6 shadow-xl flex flex-col justify-between">
            <div>
                <h3 class="text-lg font-bold text-white mb-4">Detections Metrics by Line</h3>
                <div class="overflow-y-auto max-h-[400px] border border-slate-800 rounded-xl">
                    <table class="w-full text-left border-collapse text-sm">
                        <thead>
                            <tr class="bg-slate-800/50 text-slate-400 border-b border-slate-800">
                                <th class="p-3">Line #</th>
                                <th class="p-3">GT Count</th>
                                <th class="p-3">Detected</th>
                                <th class="p-3 text-right">Error</th>
                            </tr>
                        </thead>
                        <tbody>
                            {"".join([f'''
                            <tr class="border-b border-slate-800 hover:bg-slate-800/25">
                                <td class="p-3 font-semibold text-white">Line #{idx+1}</td>
                                <td class="p-3 text-slate-300">{l["gt"]}</td>
                                <td class="p-3 text-emerald-400 font-bold">{l["det"]}</td>
                                <td class="p-3 text-right font-bold {"text-red-400" if l["det"] != l["gt"] else "text-emerald-400"}">{l["det"] - l["gt"]:+}</td>
                            </tr>
                            ''' for idx, l in enumerate(lines_summary)])}
                        </tbody>
                    </table>
                </div>
            </div>

            <div class="mt-6 border-t border-slate-800 pt-6">
                <h4 class="text-sm font-bold text-white uppercase tracking-wider mb-3">Optimal Algorithm Settings</h4>
                <div class="grid grid-cols-2 gap-3 text-xs">
                    <div class="bg-slate-950 p-2 rounded-lg"><span class="text-slate-400">Color Channel:</span><p class="font-bold text-white mt-1">{opt_cfg.gray_conversion if opt_cfg else "N/A"}</p></div>
                    <div class="bg-slate-950 p-2 rounded-lg"><span class="text-slate-400">Pre-Filter:</span><p class="font-bold text-white mt-1">{opt_cfg.pre_filter if opt_cfg else "N/A"}</p></div>
                    <div class="bg-slate-950 p-2 rounded-lg"><span class="text-slate-400">Profile Type:</span><p class="font-bold text-white mt-1">{opt_cfg.profile_type if opt_cfg else "N/A"} (h={opt_cfg.band_width if opt_cfg else 1})</p></div>
                    <div class="bg-slate-950 p-2 rounded-lg"><span class="text-slate-400">Method:</span><p class="font-bold text-white mt-1">{opt_cfg.detection_method if opt_cfg else "N/A"}</p></div>
                </div>
            </div>
        </div>
    </div>

    <footer class="text-center border-t border-slate-800 pt-6 text-slate-500 text-xs">
        <p>Log Conteo Systems - Production Report (Offline-Capable Bundle)</p>
    </footer>
</body>
</html>
"""
    
    # Save a temporary HTML report file to let FastAPI serve it
    report_filename = f"report_{image_id}.html"
    report_path = os.path.join(UPLOAD_DIR, report_filename)
    
    with open(report_path, "w", encoding="utf-8") as rf:
        rf.write(html_content)
        
    return FileResponse(
        path=report_path,
        filename=f"conteo_report_{img_record.filename}.html",
        media_type="text/html"
    )

@app.get("/api/image/{image_id}/export_json")
def export_json(image_id: int, db: Session = Depends(get_db)):
    """
    Exports the complete working session (image info, configs, coordinates) as JSON.
    """
    img_record = db.query(ImageRecord).filter(ImageRecord.id == image_id).first()
    if not img_record:
        raise HTTPException(status_code=404, detail="Image not found")
        
    lines = db.query(LineRecord).filter(LineRecord.image_id == image_id).all()
    opt_cfg = db.query(OptimizationConfig).filter(OptimizationConfig.image_id == image_id).first()
    
    session_data = {
        "version": "1.0",
        "image": {
            "filename": img_record.filename,
            "gps_lat": img_record.gps_lat,
            "gps_lon": img_record.gps_lon,
            "gps_alt": img_record.gps_alt,
            "status": img_record.status,
            "total_detected": img_record.total_detected,
            "total_gt": img_record.total_gt
        },
        "lines": [{
            "p1_x": l.p1_x, "p1_y": l.p1_y,
            "p2_x": l.p2_x, "p2_y": l.p2_y,
            "ground_truth": l.ground_truth,
            "detected_count": l.detected_count
        } for l in lines],
        "optimal_config": {
            "gray_conversion": opt_cfg.gray_conversion,
            "pre_filter": opt_cfg.pre_filter,
            "profile_type": opt_cfg.profile_type,
            "band_width": opt_cfg.band_width,
            "distance_mode": opt_cfg.distance_mode,
            "detection_method": opt_cfg.detection_method,
            "wape": opt_cfg.wape,
            "mae": opt_cfg.mae
        } if opt_cfg else None
    }
    
    return session_data

@app.post("/api/import_json")
async def import_json(request: Request, db: Session = Depends(get_db)):
    """
    Imports a JSON session backup, binding it to an existing matching image,
    or creating a stub image entry.
    """
    try:
        session_data = await request.json()
        img_info = session_data["image"]
        
        # Check if image with same filename already exists in records
        img_record = db.query(ImageRecord).filter(ImageRecord.filename == img_info["filename"]).first()
        
        if not img_record:
            # Create a stub entry
            img_record = ImageRecord(
                filename=img_info["filename"],
                filepath=os.path.join(UPLOAD_DIR, img_info["filename"]), # Assume filepath exists or will be uploaded
                gps_lat=img_info.get("gps_lat"),
                gps_lon=img_info.get("gps_lon"),
                gps_alt=img_info.get("gps_alt"),
                status=img_info.get("status", "Pending Lines"),
                total_detected=img_info.get("total_detected", 0),
                total_gt=img_info.get("total_gt", 0)
            )
            db.add(img_record)
            db.commit()
            db.refresh(img_record)
        else:
            img_record.gps_lat = img_info.get("gps_lat", img_record.gps_lat)
            img_record.gps_lon = img_info.get("gps_lon", img_record.gps_lon)
            img_record.gps_alt = img_info.get("gps_alt", img_record.gps_alt)
            img_record.status = img_info.get("status", img_record.status)
            img_record.total_detected = img_info.get("total_detected", img_record.total_detected)
            img_record.total_gt = img_info.get("total_gt", img_record.total_gt)
            db.commit()
            
        # Re-populate lines
        db.query(LineRecord).filter(LineRecord.image_id == img_record.id).delete()
        for l in session_data.get("lines", []):
            db_line = LineRecord(
                image_id=img_record.id,
                p1_x=l["p1_x"],
                p1_y=l["p1_y"],
                p2_x=l["p2_x"],
                p2_y=l["p2_y"],
                ground_truth=l["ground_truth"],
                detected_count=l.get("detected_count", 0)
            )
            db.add(db_line)
            
        # Re-populate optimal config
        db.query(OptimizationConfig).filter(OptimizationConfig.image_id == img_record.id).delete()
        cfg = session_data.get("optimal_config")
        if cfg:
            db_cfg = OptimizationConfig(
                image_id=img_record.id,
                gray_conversion=cfg["gray_conversion"],
                pre_filter=cfg["pre_filter"],
                profile_type=cfg["profile_type"],
                band_width=cfg["band_width"],
                distance_mode=cfg["distance_mode"],
                detection_method=cfg["detection_method"],
                wape=cfg["wape"],
                mae=cfg["mae"]
            )
            db.add(db_cfg)
            
        db.commit()
        return {"success": True, "image_id": img_record.id}
    except Exception as e:
        db.rollback()
        logger.error(f"Failed to import JSON session: {e}")
        return JSONResponse(status_code=400, content={"success": False, "detail": str(e)})
