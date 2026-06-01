import numpy as np
from app.core import calcular_distancia_fourier, detectar_picos

def test_fourier_distance_estimation():
    print("--- Running Fourier Distance Estimation Test ---")
    
    # 1. Generate an artificial signal representing regular log piles
    # We create a clean sine wave of period T = 12 pixels, length N = 240 pixels
    T_target = 12.0
    N = 240
    t = np.arange(N)
    
    # Grayscale intensities fluctuate between 50 and 200 (wood color contrast)
    sine_wave = 125 + 75 * np.sin(2 * np.pi * t / T_target)
    
    # Add a global lighting slope gradient (e.g. shadow fading) to test detrending/filtering
    slope = 0.3 * t
    profile_with_slope = sine_wave + slope
    
    # Estimate period
    adaptive_dist = calcular_distancia_fourier(profile_with_slope)
    estimated_period = adaptive_dist / 0.6
    
    print(f"Target spacing period: {T_target} px")
    print(f"Fourier estimated period: {estimated_period:.2f} px")
    print(f"Fourier calculated minimum peak-to-peak spacing: {adaptive_dist:.2f} px")
    
    # Allow small numerical variance (e.g., +/- 1.5 pixels)
    assert abs(estimated_period - T_target) < 1.5, f"Fourier failed! Estimated period: {estimated_period}"
    print("Success: Fourier spatial period estimation is mathematically correct!")

def test_peak_detection():
    print("\n--- Running Peak Detection Test ---")
    
    T_target = 12.0
    N = 120
    t = np.arange(N)
    # Sine wave representing 10 peaks
    profile = 125 + 75 * np.sin(2 * np.pi * t / T_target)
    
    p1 = (0, 0)
    p2 = (N, 0)
    
    # Centers of gravity (valley finder)
    peak_indices_cog, peak_coords_cog, _, _ = detectar_picos(profile, p1, p2, "centers_of_gravity", min_dist=8.0)
    
    # Direct peaks
    peak_indices_dir, peak_coords_dir, _, _ = detectar_picos(profile, p1, p2, "direct_peaks", min_dist=8.0)
    
    print(f"Number of target valleys (dark intervals): 10")
    print(f"Detected valleys (centers_of_gravity): {len(peak_indices_cog)}")
    print(f"Detected peaks (direct_peaks): {len(peak_indices_dir)}")
    
    # Valleys occur when sin(x) is minimum (around 9-10 valleys found in 120 pixels, yielding 10 logs with boundaries)
    assert len(peak_indices_cog) == 10, f"Centers of gravity failed! Found: {len(peak_indices_cog)}"
    assert len(peak_indices_dir) == 10, f"Direct peaks failed! Found: {len(peak_indices_dir)}"
    print("Success: Peak detection algorithms are mathematically correct!")

if __name__ == "__main__":
    test_fourier_distance_estimation()
    test_peak_detection()
    print("\nAll core mathematical algorithms verified successfully!")
