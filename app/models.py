from sqlalchemy import Column, Integer, String, Float, DateTime, ForeignKey, UniqueConstraint
from sqlalchemy.orm import relationship
from datetime import datetime
from .database import Base

class ImageRecord(Base):
    __tablename__ = "image_records"

    id = Column(Integer, primary_key=True, index=True)
    filename = Column(String, nullable=False)
    filepath = Column(String, nullable=False)
    upload_date = Column(DateTime, default=datetime.utcnow)
    status = Column(String, default="Pending Lines")  # "Pending Lines", "Optimized", "Report Generated"
    gps_lat = Column(Float, nullable=True)
    gps_lon = Column(Float, nullable=True)
    gps_alt = Column(Float, nullable=True)
    total_detected = Column(Integer, default=0)
    total_gt = Column(Integer, default=0)

    # Relationships
    lines = relationship("LineRecord", back_populates="image", cascade="all, delete-orphan")
    opt_config = relationship("OptimizationConfig", back_populates="image", uselist=False, cascade="all, delete-orphan")

class LineRecord(Base):
    __tablename__ = "line_records"

    id = Column(Integer, primary_key=True, index=True)
    image_id = Column(Integer, ForeignKey("image_records.id", ondelete="CASCADE"), nullable=False)
    p1_x = Column(Float, nullable=False)
    p1_y = Column(Float, nullable=False)
    p2_x = Column(Float, nullable=False)
    p2_y = Column(Float, nullable=False)
    ground_truth = Column(Integer, default=0)
    detected_count = Column(Integer, default=0)

    # Relationships
    image = relationship("ImageRecord", back_populates="lines")

class OptimizationConfig(Base):
    __tablename__ = "optimization_configs"

    id = Column(Integer, primary_key=True, index=True)
    image_id = Column(Integer, ForeignKey("image_records.id", ondelete="CASCADE"), unique=True, nullable=False)
    gray_conversion = Column(String, nullable=False)    # "Standard Gray", "Red Channel", "LAB L Channel", "Red-Blue Contrast"
    profile_type = Column(String, nullable=False)       # "Single Line", "Band Averaged"
    band_width = Column(Integer, nullable=False)         # 1, 5, 30, 100
    pre_filter = Column(String, nullable=False)         # "None", "Bilateral Filter"
    distance_mode = Column(String, nullable=False)       # "Adaptive Fourier", "Fixed"
    detection_method = Column(String, nullable=False)    # "centers_of_gravity", "direct_peaks"
    wape = Column(Float, nullable=False)
    mae = Column(Float, nullable=False)

    # Relationships
    image = relationship("ImageRecord", back_populates="opt_config")
