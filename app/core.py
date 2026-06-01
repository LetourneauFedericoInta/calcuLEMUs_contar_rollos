import cv2
import numpy as np
import scipy.signal
from PIL import Image
from PIL.ExifTags import TAGS, GPSTAGS
import logging

logger = logging.getLogger("core")

def safe_imread(filepath: str) -> np.ndarray:
    """
    Safely reads an image from filepath supporting all unicode/non-ASCII path names on Windows.
    """
    try:
        with open(filepath, "rb") as f:
            file_bytes = np.fromfile(f, dtype=np.uint8)
            return cv2.imdecode(file_bytes, cv2.IMREAD_COLOR)
    except Exception as e:
        logger.error(f"Failed to read image safely from {filepath}: {e}")
        return None

def process_gray_conversion(img: np.ndarray, conversion: str) -> np.ndarray:
    """
    Converts BGR image into target grayscale channels:
    - "Standard Gray": OpenCV Standard BGR2GRAY
    - "Red Channel": Channel index 2 of BGR
    - "LAB L Channel": L channel of CIELAB color space
    - "Red-Blue Contrast": Normal contrast enhancement between Red and Blue channels
    """
    if conversion == "Standard Gray":
        return cv2.cvtColor(img, cv2.COLOR_BGR2GRAY)
    elif conversion == "Red Channel":
        return img[:, :, 2]
    elif conversion == "LAB L Channel":
        return cv2.cvtColor(img, cv2.COLOR_BGR2Lab)[:, :, 0]
    elif conversion == "Red-Blue Contrast":
        # (Red - Blue) contrast with clipping to prevent underflow/overflow
        r = img[:, :, 2].astype(np.int16)
        b = img[:, :, 0].astype(np.int16)
        contrast = np.clip(r - b + 128, 0, 255).astype(np.uint8)
        return contrast
    else:
        return cv2.cvtColor(img, cv2.COLOR_BGR2GRAY)

def apply_pre_filter(img: np.ndarray, pre_filter: str) -> np.ndarray:
    """
    Applies target pre-filter (None vs Bilateral Filter).
    """
    if pre_filter == "Bilateral Filter":
        return cv2.bilateralFilter(img, d=5, sigmaColor=75, sigmaSpace=75)
    return img

def extract_exif_metadata(image_path: str) -> dict:
    """
    Extracts GPS coordinates (Latitude, Longitude, Altitude) from image EXIF headers.
    Returns a dict with: gps_lat, gps_lon, gps_alt (floats or None).
    """
    metadata = {"gps_lat": None, "gps_lon": None, "gps_alt": None}
    try:
        with Image.open(image_path) as img:
            exif = img._getexif()
            if not exif:
                return metadata

            gps_info = {}
            for tag, value in exif.items():
                decoded = TAGS.get(tag, tag)
                if decoded == "GPSInfo":
                    for gps_tag in value:
                        gps_decoded = GPSTAGS.get(gps_tag, gps_tag)
                        gps_info[gps_decoded] = value[gps_tag]
                    break

            if gps_info:
                # Helper function to parse coordinate degrees
                def _to_decimal_degrees(value, ref):
                    if not value:
                        return None
                    try:
                        d = float(value[0].numerator) / float(value[0].denominator) if hasattr(value[0], 'numerator') else float(value[0])
                        m = float(value[1].numerator) / float(value[1].denominator) if hasattr(value[1], 'numerator') else float(value[1])
                        s = float(value[2].numerator) / float(value[2].denominator) if hasattr(value[2], 'numerator') else float(value[2])
                        decimal = d + (m / 60.0) + (s / 3600.0)
                        if ref in ['S', 'W']:
                            decimal = -decimal
                        return decimal
                    except Exception as e:
                        logger.error(f"Error parsing GPS coordinates: {e}")
                        return None

                gps_lat = _to_decimal_degrees(gps_info.get("GPSLatitude"), gps_info.get("GPSLatitudeRef"))
                gps_lon = _to_decimal_degrees(gps_info.get("GPSLongitude"), gps_info.get("GPSLongitudeRef"))

                gps_alt = None
                gps_altitude = gps_info.get("GPSAltitude")
                if gps_altitude is not None:
                    try:
                        gps_alt = float(gps_altitude.numerator) / float(gps_altitude.denominator) if hasattr(gps_altitude, 'numerator') else float(gps_altitude)
                    except Exception as e:
                        logger.error(f"Error parsing GPS altitude: {e}")

                metadata["gps_lat"] = gps_lat
                metadata["gps_lon"] = gps_lon
                metadata["gps_alt"] = gps_alt
    except Exception as e:
        logger.error(f"Failed to extract EXIF from {image_path}: {e}")
    
    return metadata

def extraer_perfil_banda(img_gray: np.ndarray, p1: tuple, p2: tuple, h: int) -> np.ndarray:
    """
    Extracts pixel intensities along a band of thickness h between p1 and p2.
    Averages perpendicularly to the line direction to reduce surface texture noise.
    Uses cv2.remap with bilinear interpolation.
    """
    x1, y1 = p1
    x2, y2 = p2
    
    # Line vector
    vx = x2 - x1
    vy = y2 - y1
    L = np.sqrt(vx**2 + vy**2)
    
    if L < 1.0:
        # Fallback for ultra-short line segment to prevent division-by-zero
        return np.array([img_gray[int(clip(y1, 0, img_gray.shape[0]-1)), int(clip(x1, 0, img_gray.shape[1]-1))]], dtype=np.float32)
    
    # Unit vectors
    ux = vx / L
    uy = vy / L
    
    # Normal vector perpendicular to the line
    nx = -uy
    ny = ux
    
    N = int(np.floor(L))
    h = max(1, int(h))
    
    # Generate mapping grids
    t = np.arange(N, dtype=np.float32)
    s = np.arange(h, dtype=np.float32) - (h - 1) / 2.0
    
    # Meshgrid of samples: rows are perpendicular s, cols are longitudinal t
    t_grid, s_grid = np.meshgrid(t, s)
    
    # Calculate map coordinates
    map_x = x1 + t_grid * ux + s_grid * nx
    map_y = y1 + t_grid * uy + s_grid * ny
    
    # Perform sub-pixel bilinear remapping with replication at borders
    mapped_strip = cv2.remap(
        img_gray,
        map_x.astype(np.float32),
        map_y.astype(np.float32),
        cv2.INTER_LINEAR,
        borderMode=cv2.BORDER_REPLICATE
    )
    
    # Average across the perpendicular height axis
    profile = np.mean(mapped_strip, axis=0)
    return profile

def calcular_distancia_fourier(perfil: np.ndarray) -> float:
    """
    Estimates the spatial period along the profile using FFT.
    Subtracts the DC offset, filters the first 5% of frequencies,
    finds the dominant frequency, and returns Period * 0.6 as minimum peak spacing.
    """
    N = len(perfil)
    if N < 5:
        return 20.0 # Default fallback
    
    # Subtract DC Offset (mean) to avoid massive 0-frequency spike
    perfil_detrend = perfil - np.mean(perfil)
    
    # Apply Real FFT
    fft_vals = np.fft.rfft(perfil_detrend)
    magnitudes = np.abs(fft_vals)
    
    # Filter out ultra-low frequencies (first 5% of the spectrum) to remove lighting gradients
    cutoff = max(1, int(np.floor(0.05 * len(magnitudes))))
    magnitudes[:cutoff] = 0.0
    
    if np.sum(magnitudes) == 0:
        return 20.0 # Fallback for perfectly flat frequency spectrum
    
    # Find dominant frequency index
    k_max = np.argmax(magnitudes)
    
    if k_max == 0:
        return 20.0
    
    # The spatial period in pixel coordinates
    period = N / k_max
    
    # Adaptive minimum spacing is defined as Period * 0.6
    adaptive_dist = period * 0.6
    
    # Limit adaptive distance to reasonable physical boundaries (e.g., between 4px and N/2px)
    adaptive_dist = clip(adaptive_dist, 4.0, max(10.0, N / 2.0))
    return float(adaptive_dist)

def detectar_picos(perfil: np.ndarray, p1: tuple, p2: tuple, method: str, min_dist: float) -> tuple:
    """
    Detects logs along the profile.
    Regardless of the selected peak method, log center locations (green dots) are
    calculated as the equidistant midpoints between consecutive sub-pixel refined valleys (crevices).
    Returns:
      - peak_indices: np.ndarray of rounded integer indices representing log centers
      - peak_coords: list of [x, y] coordinates in 2D image space at the exact midpoints
      - normalized_profile: np.ndarray profile scaled to [0, 1]
      - min_dist: float, distance in pixels used
    """
    N = len(perfil)
    x1, y1 = p1
    x2, y2 = p2
    vx = x2 - x1
    vy = y2 - y1
    L = np.sqrt(vx**2 + vy**2)
    
    # Calculate unit direction vector
    ux = vx / L if L > 0 else 0
    uy = vy / L if L > 0 else 0
    
    # Min/Max normalization
    min_val = np.min(perfil)
    max_val = np.max(perfil)
    
    if max_val > min_val:
        norm_perfil = (perfil - min_val) / (max_val - min_val)
    else:
        norm_perfil = np.zeros_like(perfil)
        
    std_val = np.std(norm_perfil)
    # Avoid zero standard deviation
    if std_val < 1e-4:
        std_val = 0.1
        
    prom = 0.8 * std_val
    
    # 1. We ALWAYS locate the valleys (local minima / crevices) in the profile first.
    # Valleys are the local peaks of the inverted profile (1.0 - norm_perfil)
    inverted = 1.0 - norm_perfil
    valley_indices, _ = scipy.signal.find_peaks(
        inverted,
        distance=max(1.0, min_dist),
        prominence=prom
    )
    
    # 2. Refine each valley to sub-pixel precision using local center of gravity (centroid)
    refined_valleys = []
    for v in valley_indices:
        start = max(0, v - 3)
        end = min(N - 1, v + 3)
        indices = np.arange(start, end + 1)
        weights = inverted[start : end + 1]
        
        sum_w = np.sum(weights)
        if sum_w > 0.0:
            v_refined = float(np.sum(indices * weights) / sum_w)
        else:
            v_refined = float(v)
        refined_valleys.append(v_refined)
        
    # Sort refined valleys to ensure sequential intervals
    refined_valleys = sorted(refined_valleys)
    
    # 3. Calculate log centers using the valley-midpoint logic and boundaries
    log_centers = []
    peak_coords = []
    
    if len(refined_valleys) >= 1:
        # Check first boundary (before the first valley)
        first_v = refined_valleys[0]
        if first_v >= 0.5 * min_dist:
            log_centers.append(first_v / 2.0)
            
        # Midpoints between consecutive valleys
        for i in range(len(refined_valleys) - 1):
            c = (refined_valleys[i] + refined_valleys[i+1]) / 2.0
            log_centers.append(c)
            
        # Check last boundary (after the last valley)
        last_v = refined_valleys[-1]
        if (N - 1) - last_v >= 0.5 * min_dist:
            log_centers.append((last_v + (N - 1)) / 2.0)
            
        # Map calculated sub-pixel centers to 2D image coordinates
        for c in log_centers:
            px = x1 + c * ux
            py = y1 + c * uy
            peak_coords.append([float(px), float(py)])
            
        peak_indices = np.array([int(round(c)) for c in log_centers], dtype=np.int32)
    else:
        # Fallback if no valleys are found
        peak_indices = np.array([], dtype=np.int32)
        peak_coords = []
        
    return peak_indices, peak_coords, norm_perfil, min_dist

def clip(val, minimum, maximum):
    return min(maximum, max(minimum, val))
