import cv2
import numpy as np
import logging
from .core import extraer_perfil_banda, calcular_distancia_fourier, detectar_picos

logger = logging.getLogger("optimization")

class GridSearchEngine:
    @staticmethod
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

    @staticmethod
    def apply_pre_filter(img: np.ndarray, pre_filter: str) -> np.ndarray:
        """
        Applies target pre-filter (None vs Bilateral Filter).
        """
        if pre_filter == "Bilateral Filter":
            return cv2.bilateralFilter(img, d=5, sigmaColor=75, sigmaSpace=75)
        return img

    @classmethod
    def run_grid_search(cls, img: np.ndarray, lines_data: list) -> list:
        """
        Runs full combinatorial search evaluating combinations:
        - 4 Gray Conversions
        - 2 Pre-filters
        - 4 Profile/Band Widths (Single Line vs Band Averaged [5, 30, 100])
        - 2 Spacing/Distance modes (Fourier vs Fixed)
        - 2 Peak methods (centers_of_gravity vs direct_peaks)
        
        Evaluates metrics against user provided Ground Truths.
        Returns top 15 configuration results sorted by lowest WAPE and MAE.
        """
        if not lines_data:
            return []

        # Parameter options
        conversions = ["Standard Gray", "Red Channel", "LAB L Channel", "Red-Blue Contrast"]
        pre_filters = ["None", "Bilateral Filter"]
        profile_configs = [
            ("Single Line", 1),
            ("Band Averaged", 5),
            ("Band Averaged", 30),
            ("Band Averaged", 100)
        ]
        distance_modes = ["Adaptive Fourier", "Fixed"]
        detection_methods = ["centers_of_gravity", "direct_peaks"]

        # Precompute the 8 processed images (4 conversions x 2 filters) to maximize execution speed
        processed_images = {}
        for conv in conversions:
            base_gray = cls.process_gray_conversion(img, conv)
            for filt in pre_filters:
                processed_images[(conv, filt)] = cls.apply_pre_filter(base_gray, filt)

        # Ground truths
        gts = [line["ground_truth"] for line in lines_data]
        sum_gts = sum(gts)

        results = []

        # Combinatorial loop (128 iterations)
        for conv in conversions:
            for filt in pre_filters:
                target_img = processed_images[(conv, filt)]
                
                for prof_type, h in profile_configs:
                    # Extract profiles for all lines for this image state
                    profiles_and_endpoints = []
                    for line in lines_data:
                        p1 = (line["p1_x"], line["p1_y"])
                        p2 = (line["p2_x"], line["p2_y"])
                        profile = extraer_perfil_banda(target_img, p1, p2, h)
                        profiles_and_endpoints.append((profile, p1, p2))

                    for dist_mode in distance_modes:
                        # Precalculate distances for each line profile
                        line_distances = []
                        for profile, p1, p2 in profiles_and_endpoints:
                            if dist_mode == "Adaptive Fourier":
                                dist = calcular_distancia_fourier(profile)
                            else:
                                dist = 20.0  # Fixed standard spacing distance
                            line_distances.append(dist)

                        for method in detection_methods:
                            detected_counts = []
                            
                            # Run detection on all lines
                            for i, (profile, p1, p2) in enumerate(profiles_and_endpoints):
                                min_dist = line_distances[i]
                                peak_idx, _, _, _ = detectar_picos(profile, p1, p2, method, min_dist)
                                detected_counts.append(len(peak_idx))

                            # Calculate errors
                            errors = [det - gt for det, gt in zip(detected_counts, gts)]
                            abs_errors = [abs(err) for err in errors]
                            
                            sum_detected = sum(detected_counts)
                            mae = float(np.mean(abs_errors))
                            wape = float(sum(abs_errors) / sum_gts) if sum_gts > 0 else 0.0
                            global_abs_error = int(abs(sum_detected - sum_gts))
                            std_err = float(np.std(abs_errors))

                            config = {
                                "gray_conversion": conv,
                                "pre_filter": filt,
                                "profile_type": prof_type,
                                "band_width": h,
                                "distance_mode": dist_mode,
                                "detection_method": method,
                                "mae": mae,
                                "wape": wape,
                                "global_abs_error": global_abs_error,
                                "std_err": std_err,
                                "detected_counts": detected_counts
                            }
                            results.append(config)

        # Sort configurations: lowest WAPE first, then lowest MAE
        results.sort(key=lambda x: (x["wape"], x["mae"]))

        # Return the Top 15 configurations
        return results[:15]
