/**
 * Log Counting Visor - HTML5 Canvas Editor
 * Manages high-resolution orthomosaic viewport zooming, panning, line drawing, and drag handles.
 */

class CanvasEditor {
    constructor(canvasId, containerId, imageSrc, onLinesChanged) {
        this.canvas = document.getElementById(canvasId);
        this.ctx = this.canvas.getContext('2d');
        this.container = document.getElementById(containerId);
        this.imageSrc = imageSrc;
        this.onLinesChanged = onLinesChanged; // Callback when lines are modified

        this.img = new Image();
        this.lines = []; // Array of lines: { p1_x, p1_y, p2_x, p2_y, ground_truth, detected_count, peaks: [] }
        
        // Zoom and Pan States
        this.scale = 1.0;
        this.offsetX = 0;
        this.offsetY = 0;
        
        // Mouse State
        this.isPanning = false;
        this.isDrawing = false;
        this.isDraggingHandle = false;
        
        this.dragStart = { x: 0, y: 0 };
        this.panStart = { x: 0, y: 0 };
        this.activeTool = 'draw'; // 'draw' or 'pan'
        
        // Handle Edit States
        this.selectedLineIdx = -1;
        this.selectedHandle = null; // 'p1' or 'p2'
        this.hoveredLineIdx = -1;
        this.hoveredHandle = null;
        
        this.handleRadius = 8; // Screen px size of endpoint drag handles
        this.nearTolerance = 12; // Screen px click tolerance
        
        // Dynamic crosshair link from chart hover
        this.hoveredChartPoint = null;
        
        this.init();
    }

    init() {
        this.img.onload = () => {
            this.resetView();
            this.resizeCanvas();
            this.redraw();
        };
        this.img.src = this.imageSrc;

        // Resize Hook
        window.addEventListener('resize', () => {
            this.resizeCanvas();
            this.redraw();
        });

        // Mouse Events
        this.canvas.addEventListener('mousedown', (e) => this.handleMouseDown(e));
        this.canvas.addEventListener('mousemove', (e) => this.handleMouseMove(e));
        this.canvas.addEventListener('mouseup', (e) => this.handleMouseUp(e));
        this.canvas.addEventListener('mouseleave', (e) => this.handleMouseUp(e));
        this.canvas.addEventListener('wheel', (e) => this.handleWheel(e), { passive: false });
        
        // Prevents default context menu to enable right-click panning
        this.canvas.addEventListener('contextmenu', (e) => e.preventDefault());

        // Keyboard Listener for deleting selected line (Delete or Backspace)
        window.addEventListener('keydown', (e) => {
            if ((e.key === 'Delete' || e.key === 'Backspace') && this.selectedLineIdx !== -1) {
                // Prevent deletion if user is typing inside an input field!
                if (document.activeElement.tagName === 'INPUT' || document.activeElement.tagName === 'TEXTAREA') {
                    return;
                }
                e.preventDefault();
                this.deleteLine(this.selectedLineIdx);
                this.selectedLineIdx = -1;
            }
        });
    }

    setTool(tool) {
        this.activeTool = tool;
        this.canvas.className = tool === 'pan' ? 'cursor-grab' : 'cursor-crosshair';
    }

    resizeCanvas() {
        const width = this.container.clientWidth;
        const height = this.container.clientHeight;
        
        // High-DPI screen adjustments
        const dpr = window.devicePixelRatio || 1;
        this.canvas.width = width * dpr;
        this.canvas.height = height * dpr;
        this.canvas.style.width = width + 'px';
        this.canvas.style.height = height + 'px';
        this.ctx.scale(dpr, dpr);
    }

    resetView() {
        if (!this.img.width) return;
        
        const containerW = this.container.clientWidth;
        const containerH = this.container.clientHeight;
        
        // Compute best scale-to-fit
        const scaleX = containerW / this.img.width;
        const scaleY = containerH / this.img.height;
        this.scale = Math.min(scaleX, scaleY) * 0.95; // 95% padding
        
        // Center image
        this.offsetX = (containerW - this.img.width * this.scale) / 2;
        this.offsetY = (containerH - this.img.height * this.scale) / 2;
    }

    zoom(amount, clientX, clientY) {
        const rect = this.canvas.getBoundingClientRect();
        const mouseX = clientX - rect.left;
        const mouseY = clientY - rect.top;
        
        // Map mouse screen location to raw image location before zoom
        const imgX = (mouseX - this.offsetX) / this.scale;
        const imgY = (mouseY - this.offsetY) / this.scale;
        
        const minScale = 0.05;
        const maxScale = 20.0;
        const newScale = Math.min(maxScale, Math.max(minScale, this.scale * amount));
        
        this.scale = newScale;
        
        // Adjust offsets to keep mouse focused on the same point
        this.offsetX = mouseX - imgX * this.scale;
        this.offsetY = mouseY - imgY * this.scale;
        
        this.redraw();
    }

    // Coordinates conversion: Image pixel coordinate space -> HTML5 Screen coordinate space
    toScreen(imgX, imgY) {
        return {
            x: imgX * this.scale + this.offsetX,
            y: imgY * this.scale + this.offsetY
        };
    }

    // Coordinates conversion: HTML5 Screen coordinate space -> Image pixel coordinate space
    toImage(screenX, screenY) {
        return {
            x: (screenX - this.offsetX) / this.scale,
            y: (screenY - this.offsetY) / this.scale
        };
    }

    // Mathematical projection: distance from point p to line segment between v and w (screen pixels)
    getDistanceToSegment(p, v, w) {
        const l2 = (v.x - w.x)**2 + (v.y - w.y)**2;
        if (l2 === 0) return Math.sqrt((p.x - v.x)**2 + (p.y - v.y)**2);
        let t = ((p.x - v.x) * (w.x - v.x) + (p.y - v.y) * (w.y - v.y)) / l2;
        t = Math.max(0, Math.min(1, t));
        return Math.sqrt((p.x - (v.x + t * (w.x - v.x)))**2 + (p.y - (v.y + t * (w.y - v.y)))**2);
    }

    handleMouseDown(e) {
        const rect = this.canvas.getBoundingClientRect();
        const clientX = e.clientX - rect.left;
        const clientY = e.clientY - rect.top;
        
        const imgPos = this.toImage(clientX, clientY);
        
        // Right click OR Left click in Pan Mode triggers panning
        if (e.button === 2 || (e.button === 0 && this.activeTool === 'pan')) {
            this.isPanning = true;
            this.panStart = { x: clientX, y: clientY };
            this.canvas.className = 'cursor-grabbing';
            return;
        }

        if (e.button === 0 && this.activeTool === 'draw') {
            // 1. Check if user clicked near an endpoint handle first (handles editing has priority)
            if (this.hoveredLineIdx !== -1 && this.hoveredHandle) {
                this.isDraggingHandle = true;
                this.selectedLineIdx = this.hoveredLineIdx;
                this.selectedHandle = this.hoveredHandle;
                return;
            }

            // 2. Check if user clicked near any line segment to SELECT it instead of drawing a new line
            let minSegmentDist = Infinity;
            let clickedSegmentIdx = -1;
            for (let i = 0; i < this.lines.length; i++) {
                const line = this.lines[i];
                const scrP1 = this.toScreen(line.p1_x, line.p1_y);
                const scrP2 = this.toScreen(line.p2_x, line.p2_y);
                const dist = this.getDistanceToSegment({ x: clientX, y: clientY }, scrP1, scrP2);
                if (dist < minSegmentDist) {
                    minSegmentDist = dist;
                    clickedSegmentIdx = i;
                }
            }

            if (clickedSegmentIdx !== -1 && minSegmentDist < this.nearTolerance) {
                // Click was near an existing line segment - select it!
                this.selectedLineIdx = clickedSegmentIdx;
                this.onLinesChanged(this.lines); // Trigger callback to update highlights in table
                this.redraw();
                return;
            }

            // 3. User clicked on empty space - start drawing a new line
            this.selectedLineIdx = -1; // Deselect active line
            this.isDrawing = true;
            this.dragStart = imgPos;
            
            // Push active line skeleton
            this.lines.push({
                p1_x: imgPos.x,
                p1_y: imgPos.y,
                p2_x: imgPos.x,
                p2_y: imgPos.y,
                ground_truth: 0,
                detected_count: 0,
                peaks: []
            });
            this.onLinesChanged(this.lines); // Update table immediately to show drawing skeleton
            this.redraw();
        }
    }

    handleMouseMove(e) {
        const rect = this.canvas.getBoundingClientRect();
        const clientX = e.clientX - rect.left;
        const clientY = e.clientY - rect.top;
        
        const imgPos = this.toImage(clientX, clientY);

        if (this.isPanning) {
            const dx = clientX - this.panStart.x;
            const dy = clientY - this.panStart.y;
            this.offsetX += dx;
            this.offsetY += dy;
            this.panStart = { x: clientX, y: clientY };
            this.redraw();
            return;
        }

        if (this.isDrawing) {
            const activeLine = this.lines[this.lines.length - 1];
            // Clip coordinates within image borders
            activeLine.p2_x = Math.max(0, Math.min(this.img.width, imgPos.x));
            activeLine.p2_y = Math.max(0, Math.min(this.img.height, imgPos.y));
            this.redraw();
            return;
        }

        if (this.isDraggingHandle) {
            const activeLine = this.lines[this.selectedLineIdx];
            const px = Math.max(0, Math.min(this.img.width, imgPos.x));
            const py = Math.max(0, Math.min(this.img.height, imgPos.y));
            
            if (this.selectedHandle === 'p1') {
                activeLine.p1_x = px;
                activeLine.p1_y = py;
            } else {
                activeLine.p2_x = px;
                activeLine.p2_y = py;
            }
            this.redraw();
            return;
        }

        // Default mouse hover: search if hovered near an endpoint handle
        this.checkHoverState(clientX, clientY);
    }

    handleMouseUp(e) {
        if (this.isPanning) {
            this.isPanning = false;
            this.setTool(this.activeTool);
            return;
        }

        if (this.isDrawing) {
            this.isDrawing = false;
            
            // Clean up ultra-short click lines (accidental drawings under 8px)
            const activeLine = this.lines[this.lines.length - 1];
            const dx = activeLine.p2_x - activeLine.p1_x;
            const dy = activeLine.p2_y - activeLine.p1_y;
            const len = Math.sqrt(dx*dx + dy*dy);
            
            if (len < 8) {
                this.lines.pop();
            } else {
                // Set default GT count based on previous line or simple 0
                activeLine.ground_truth = this.lines.length > 1 ? this.lines[this.lines.length - 2].ground_truth : 10;
                this.onLinesChanged(this.lines);
            }
            this.redraw();
            return;
        }

        if (this.isDraggingHandle) {
            this.isDraggingHandle = false;
            this.selectedHandle = null; // Retain active line selection after dragging handle ends
            this.onLinesChanged(this.lines);
            this.redraw();
        }
    }

    handleWheel(e) {
        e.preventDefault();
        const factor = e.deltaY < 0 ? 1.1 : 0.9;
        this.zoom(factor, e.clientX, e.clientY);
    }

    checkHoverState(screenX, screenY) {
        this.hoveredLineIdx = -1;
        this.hoveredHandle = null;
        let cursorStyle = this.activeTool === 'pan' ? 'grab' : 'crosshair';

        // 1. First check if mouse is near a handle of the currently selected line (only selected line shows handles)
        if (this.selectedLineIdx !== -1 && this.lines[this.selectedLineIdx]) {
            const line = this.lines[this.selectedLineIdx];
            const scrP1 = this.toScreen(line.p1_x, line.p1_y);
            const scrP2 = this.toScreen(line.p2_x, line.p2_y);

            const distP1 = Math.sqrt((screenX - scrP1.x)**2 + (screenY - scrP1.y)**2);
            const distP2 = Math.sqrt((screenX - scrP2.x)**2 + (screenY - scrP2.y)**2);

            if (distP1 < this.nearTolerance) {
                this.hoveredLineIdx = this.selectedLineIdx;
                this.hoveredHandle = 'p1';
                cursorStyle = 'nesw-resize';
            } else if (distP2 < this.nearTolerance) {
                this.hoveredLineIdx = this.selectedLineIdx;
                this.hoveredHandle = 'p2';
                cursorStyle = 'nesw-resize';
            }
        }

        // 2. If not hovering a handle, check if hovering over any other line segment to show a hand selection pointer
        if (this.hoveredLineIdx === -1 && this.activeTool === 'draw') {
            for (let i = 0; i < this.lines.length; i++) {
                const line = this.lines[i];
                const scrP1 = this.toScreen(line.p1_x, line.p1_y);
                const scrP2 = this.toScreen(line.p2_x, line.p2_y);
                const dist = this.getDistanceToSegment({ x: screenX, y: screenY }, scrP1, scrP2);
                if (dist < this.nearTolerance) {
                    cursorStyle = 'pointer';
                    break;
                }
            }
        }
        
        if (this.canvas.style.cursor !== cursorStyle && !this.isPanning) {
            this.canvas.style.cursor = cursorStyle;
        }
    }

    deleteLine(idx) {
        if (idx >= 0 && idx < this.lines.length) {
            this.lines.splice(idx, 1);
            this.onLinesChanged(this.lines);
            this.redraw();
        }
    }

    clearAllLines() {
        this.lines = [];
        this.onLinesChanged(this.lines);
        this.redraw();
    }

    redraw() {
        if (!this.img.width) return;
        
        // Clear canvas
        this.ctx.clearRect(0, 0, this.canvas.width, this.canvas.height);
        
        // Draw main high-resolution image
        this.ctx.drawImage(
            this.img, 
            0, 0, this.img.width, this.img.height,
            this.offsetX, this.offsetY, this.img.width * this.scale, this.img.height * this.scale
        );
        
        // Draw reference lines
        this.lines.forEach((line, index) => {
            const p1 = this.toScreen(line.p1_x, line.p1_y);
            const p2 = this.toScreen(line.p2_x, line.p2_y);
            const isSelected = (index === this.selectedLineIdx);
            
            // Draw main transverse line (blue if selected, red otherwise)
            this.ctx.beginPath();
            this.ctx.moveTo(p1.x, p1.y);
            this.ctx.lineTo(p2.x, p2.y);
            this.ctx.strokeStyle = isSelected ? '#3b82f6' : '#ef4444'; 
            this.ctx.lineWidth = isSelected ? 4.5 : 2.5;
            this.ctx.lineCap = 'round';
            this.ctx.shadowColor = 'rgba(0, 0, 0, 0.6)';
            this.ctx.shadowBlur = 6;
            this.ctx.stroke();
            this.ctx.shadowBlur = 0; // Reset shadow

            // Draw line numeric labels with active HUD statistics: Line X | Det: Y | GT: Z
            const midX = (p1.x + p2.x) / 2;
            const midY = (p1.y + p2.y) / 2;
            this.ctx.font = 'bold 11px Outfit, sans-serif';
            
            let labelText = `Line #${index + 1}`;
            if (line.detected_count !== undefined && line.detected_count > 0) {
                labelText = `Line #${index + 1} | Det: ${line.detected_count} | GT: ${line.ground_truth || 0}`;
            }
            const textWidth = this.ctx.measureText(labelText).width;
            
            // Render glassmorphic pill background for label (semi-transparent slate HUD)
            this.ctx.fillStyle = 'rgba(15, 23, 42, 0.85)';
            this.ctx.strokeStyle = isSelected ? '#3b82f6' : 'rgba(16, 185, 129, 0.4)'; // Blue border if selected, Emerald if active
            this.ctx.lineWidth = 1;
            
            this.drawRoundedRect(
                midX - textWidth/2 - 8, 
                midY - 18, 
                textWidth + 16, 
                16, 
                4, 
                true, 
                true
            );
            
            this.ctx.fillStyle = '#f8fafc';
            this.ctx.textAlign = 'center';
            this.ctx.fillText(labelText, midX, midY - 6);

            // Draw solid green peaks/detections
            if (line.peaks && line.peaks.length > 0) {
                line.peaks.forEach(peak => {
                    const scrPeak = this.toScreen(peak[0], peak[1]);
                    
                    this.ctx.beginPath();
                    this.ctx.arc(scrPeak.x, scrPeak.y, 5, 0, Math.PI * 2);
                    this.ctx.fillStyle = '#10b981'; // Emerald green
                    this.ctx.strokeStyle = '#000000'; // Black thick outline
                    this.ctx.lineWidth = 1.5;
                    this.ctx.fill();
                    this.ctx.stroke();
                });
            }

            // Draw grab handles at end points ONLY if the line is selected or hovered
            const isHovered = (this.hoveredLineIdx === index);
            if (isSelected || isHovered) {
                this.drawHandle(p1.x, p1.y, (isHovered && this.hoveredHandle === 'p1'), isSelected);
                this.drawHandle(p2.x, p2.y, (isHovered && this.hoveredHandle === 'p2'), isSelected);
            }
        });

        // Draw interactive crosshair target when hovering over the signal intensity chart
        if (this.hoveredChartPoint) {
            const scrPoint = this.toScreen(this.hoveredChartPoint.x, this.hoveredChartPoint.y);
            
            // Draw crosshairs
            this.ctx.beginPath();
            this.ctx.strokeStyle = '#f97316'; // Vivid high-visibility orange
            this.ctx.lineWidth = 1.5;
            this.ctx.setLineDash([3, 3]); // Dashed line
            
            // Horizontal crosshair line (36px long)
            this.ctx.moveTo(scrPoint.x - 18, scrPoint.y);
            this.ctx.lineTo(scrPoint.x + 18, scrPoint.y);
            
            // Vertical crosshair line (36px long)
            this.ctx.moveTo(scrPoint.x, scrPoint.y - 18);
            this.ctx.lineTo(scrPoint.x, scrPoint.y + 18);
            this.ctx.stroke();
            this.ctx.setLineDash([]); // Reset line dash
            
            // Pulsing target outer ring
            this.ctx.beginPath();
            this.ctx.arc(scrPoint.x, scrPoint.y, 11, 0, Math.PI * 2);
            this.ctx.strokeStyle = '#ffffff';
            this.ctx.lineWidth = 2.5;
            this.ctx.shadowColor = 'rgba(0, 0, 0, 0.6)';
            this.ctx.shadowBlur = 4;
            this.ctx.stroke();
            this.ctx.shadowBlur = 0; // Reset shadow
            
            // Inner center target point
            this.ctx.beginPath();
            this.ctx.arc(scrPoint.x, scrPoint.y, 4.5, 0, Math.PI * 2);
            this.ctx.fillStyle = '#f97316';
            this.ctx.strokeStyle = '#ffffff';
            this.ctx.lineWidth = 1;
            this.ctx.fill();
            this.ctx.stroke();
        }
    }

    drawHandle(x, y, isHovered, isSelected = false) {
        this.ctx.beginPath();
        this.ctx.arc(x, y, this.handleRadius, 0, Math.PI * 2);
        this.ctx.fillStyle = isHovered ? '#10b981' : '#ffffff'; // Hover is Emerald, normal white
        this.ctx.strokeStyle = isSelected ? '#3b82f6' : '#ef4444'; // Blue borders if selected, Red otherwise
        this.ctx.lineWidth = 2.5;
        this.ctx.shadowColor = 'rgba(0, 0, 0, 0.4)';
        this.ctx.shadowBlur = 4;
        this.ctx.fill();
        this.ctx.stroke();
        this.ctx.shadowBlur = 0; // Reset
    }

    drawRoundedRect(x, y, width, height, radius, fill, stroke) {
        this.ctx.beginPath();
        this.ctx.moveTo(x + radius, y);
        this.ctx.lineTo(x + width - radius, y);
        this.ctx.quadraticCurveTo(x + width, y, x + width, y + radius);
        this.ctx.lineTo(x + width, y + height - radius);
        this.ctx.quadraticCurveTo(x + width, y + height, x + width - radius, y + height);
        this.ctx.lineTo(x + radius, y + height);
        this.ctx.quadraticCurveTo(x, y + height, x, y + height - radius);
        this.ctx.lineTo(x, y + radius);
        this.ctx.quadraticCurveTo(x, y, x + radius, y);
        this.ctx.closePath();
        if (fill) this.ctx.fill();
        if (stroke) this.ctx.stroke();
    }
}
