import numpy as np
from scipy.stats import norm
from scipy.special import gamma
from scipy.interpolate import griddata

def rotate_to_wind(x, y, wind_dir):
    """Rotate coordinates to align with wind direction."""
    theta = np.radians(wind_dir)
    x_rot = x * np.cos(theta) + y * np.sin(theta)
    y_rot = -x * np.sin(theta) + y * np.cos(theta)
    return x_rot, y_rot

def patch_index(patch_map):
    """Get unique indices from patch map."""
    return np.unique(patch_map[~np.isnan(patch_map)])

def calc_fp_basic(ustar, Lo, sv, zo, zH, d, windir, FX, FY, PatchMap=None, 
                 models=[1,1,1], mscale=0, contour_marks=None, Tdist=0, 
                 Twindir=0, ubar=0, h=0):
    """
    Calculate footprint using multiple models.
    
    Parameters:
        ustar: friction velocity [m/s]
        Lo: Obukhov stability length [m]
        sv: fluctuation of lateral wind [m/s] (approx. 2 times ustar)
        zo: momentum roughness length [m]
        zH: height of the measurement [m]
        d: displacement height [m]
        windir: wind direction, from north, degrees (wind coming FROM)
        FX, FY: meshgrid coordinates of mask map
        models: list of which models to run [Hsieh, Kljun, K&M]
        mscale: 0 for linear scaling, 1 for exponential
    """
    
    # Initialize output structure
    FPall = [{'name': 'Hsieh'}, {'name': 'Kljun'}, {'name': 'K&M'}]
    
    # Overall Parameters
    zm = zH - d  # Effective height
    Lx = 100 * zm  # Initial length of along-wind footprint
    k = 0.4  # von Karman constant
    zL = zm / Lo  # Stability coefficient
    
    # Rotate target point
    Txmax, Tymax = rotate_to_wind(Tdist, 0, Twindir)
    
    # Prepare coordinate systems
    FX2, FY2 = np.meshgrid(FX, FY)
    Fxprime, Fyprime = rotate_to_wind(FX2, FY2, windir)
    Rl = len(FY)
    
    # Lateral dispersion width
    ywidth0 = np.floor(zo * (0.3 * (sv/ustar) * (Lx/zo)**0.86) / 1.5)
    ywidth = ywidth0 * 3
    
    # Hsieh model parameters
    zu = zm * (np.log(zm/zo) - 1 + zo/zm)  # New height scale, Eq 13.5 Hsieh (2000)
    
    # Coefficients D and P according to stability
    P = [0.59, 1, 1.33]
    D = [0.28, 0.97, 2.44]
    
    stab = zu / Lo
    thresh = 0.04
    
    if stab < -thresh:
        ii = 0
    elif abs(stab) < thresh:
        ii = 1
    elif stab > thresh:
        ii = 2
    
    D1 = D[ii]
    P1 = P[ii]
    
    F2H = (D1 / (0.105 * k**2)) * (zm**-1 * abs(Lo)**(1-P1) * zu**P1)  # Eq 20, Hsieh
    Xm = np.ceil(F2H * zm)
    
    # Initialize all models to None
    footHsieh = footKljun = footKM = None

    # Calculate pixel values at target point
    pxR = {}
    closestIndexX = np.argmin(np.abs(FX2[0, :] - Txmax))
    closestIndexY = np.argmin(np.abs(FY2[::-1, 0] - Tymax))

    if models[0] and footHsieh is not None:
        pxR['H'] = footHsieh[closestIndexY, closestIndexX]
    if models[1] and footKljun is not None:
        pxR['K'] = footKljun[closestIndexY, closestIndexX]
    if models[2] and footKM is not None:
        pxR['KM'] = footKM[closestIndexY, closestIndexX]

    # Calculate patch sums if patch map provided
    # Probably not a land cover map if too many classes
    path_max_limit = 20
    if PatchMap is not None:
        PI = patch_index(PatchMap)
        if len(PI) < path_max_limit:
            for i in range(len(PI)):
                if models[0] and footHsieh is not None:
                    FPall[0]['PatchSum'] = np.nansum(
                        footHsieh * (PatchMap == PI[i]))
                if models[1] and footKljun is not None:
                    FPall[1]['PatchSum'] = np.nansum(
                        footKljun * (PatchMap == PI[i]))
                if models[2] and footKM is not None:
                    FPall[2]['PatchSum'] = np.nansum(
                        footKM * (PatchMap == PI[i]))

    return FPall, pxR


def hsieh():
    """Hsieh model"""
    if mscale == 0:  # linear
        bin_size = max(1, np.round(F2H * zm / 500, 0))
        x = np.arange(0.001, Xm + bin_size, bin_size)  # Avoid eps
    elif mscale == 1:  # exponential
        bin_size = 0.1
        x = np.exp(np.arange(0, np.log(Xm * 1.1) + bin_size))
    
    # Cross-wind integrated footprint (Eq 17 Hsieh)
    T2 = (-1 / k**2) * (D1 * zu**P1 * abs(Lo)**(1-P1)) / x
    Fp = -(T2 / x) * np.exp(T2)
    
    # 2D expansion (Detto 2006)
    nn = len(x)
    sy = zo * 0.3 * (sv/ustar) * (x/zo)**0.86  # Eq B4 Detto
    
    if mscale == 0:  # linear
        y = np.arange(-ywidth, ywidth + bin_size, bin_size)
    elif mscale == 1:  # exponential
        y_pos = np.exp(np.arange(0, np.log(ywidth) + bin_size))
        y = np.concatenate([-np.flip(y_pos), [0], y_pos])
    
    # Create footprint matrix
    footo = np.zeros((nn, len(y)))
    for i in range(nn):
        footo[i,:] = Fp[i] * norm.pdf(y, 0, sy[i])
    
    # Unrotated footprint
    foot = footo.T
    
    if not np.all(y == 0):
        xx, yy = np.meshgrid(x, y)
        foot_rotate = griddata((xx.flatten(), yy.flatten()), foot.flatten(), 
                                (Fxprime.flatten(), Fyprime.flatten()), 
                                method='linear')
        foot_rotate = foot_rotate.reshape(FX2.shape)
        
        if mscale == 0:
            dx = x[1] - x[0]
            dy = y[1] - y[0]
            binnorm = (np.nansum(foot) * dx * dy) / np.nansum(foot_rotate)
        elif mscale == 1:
            dx = np.concatenate([[x[0]], np.diff(x)])
            dy = np.concatenate([[abs(y[1]-y[0])], np.diff(y)])
            dxx, dyy = np.meshgrid(dx, dy)
            DD = foot * dxx * dyy
            binnorm = np.nansum(DD) / np.nansum(foot_rotate)
        
        footHsieh = foot_rotate * binnorm
        
        # Distance of maximum contribution (Eq 19 Hsieh)
        HXmax = D1 * zu**P1 * abs(Lo)**(1-P1) / (2 * 0.4**2)
        Hxmax, Hymax = rotate_to_wind(HXmax, 0, windir)
        
        # Store results
        FPall[0]['foot'] = footHsieh
        FPall[0]['Xmax'] = HXmax

def korman_and_meixner():
    if mscale == 0:  # linear
        bin_size = max(1, np.round(F2H * zm / 500, 0))
        x = np.arange(0.001, Xm + bin_size, bin_size)
    elif mscale == 1:  # exponential
        bin_size = 0.1
        x = np.exp(np.arange(0, np.log(Xm * 1.1) + bin_size))
    
    nn = len(x)
    
    # Stability parameters
    if zL > 0:
        zt = 0
        phim = 1 + 5 * zL
        phic = 1 + 5 * zL
        psim = 5 * zL
        n = 1 / phic
    else:
        zt = (1 - 16 * zL)**0.25
        phim = (1 - 16 * zL)**-0.25
        phic = (1 - 16 * zL)**-0.5
        psim = -2 * np.log((1 + zt)/2) - np.log((1 + zt**2)/2) + 2 * np.arctan(zt) - np.pi/2
        n = (1 - 24 * zL) / (1 - 16 * zL)
    
    uz = (ustar / k) * (np.log(zm / zo) + psim)
    eddydif = k * ustar * zm / phic
    m = ustar * phim / (0.41 * uz)
    r = 2 + m - n
    mu = (1 + m) / r
    fgamma = gamma(mu)
    alpu = uz / (zm**m)
    alpk = eddydif / (zm**n)
    xi = alpu * zm**r / (r**2 * alpk)
    FpKM = (1/fgamma) * (xi**mu) / (x**(1 + mu)) * np.exp(-xi/x)
    
    # Lateral dispersion
    if mscale == 0:  # linear
        y = np.arange(-ywidth, ywidth + bin_size, bin_size)
    elif mscale == 1:  # exponential
        y_pos = np.exp(np.arange(0, np.log(ywidth) + bin_size))
        y = np.concatenate([-np.flip(y_pos), [0], y_pos])
    
    xx, yy = np.meshgrid(x, y)
    footKMo = np.zeros((nn, len(y)))
    
    # Lateral dispersion parameters
    uPlume = gamma(mu) / gamma(1/r)
    uPlume *= ((r**2 * alpk / alpu)**(m/r))
    auPlume = uPlume * (alpu * (x**(m/r)))
    sigmaY = sv * x / auPlume
    
    for i in range(nn):
        footKMo[i,:] = FpKM[i] * norm.pdf(y, 0, sigmaY[i])
    
    footF = footKMo.T
    
    if not np.all(y == 0):
        foot_rotate_KM = griddata((xx.flatten(), yy.flatten()), footF.flatten(), 
                                (Fxprime.flatten(), Fyprime.flatten()), 
                                method='linear')
        foot_rotate_KM = foot_rotate_KM.reshape(FX2.shape)
        
        if mscale == 0:
            dx = x[1] - x[0]
            dy = y[1] - y[0]
            binnorm = (np.nansum(footF) * dx * dy) / np.nansum(foot_rotate_KM)
        elif mscale == 1:
            dx = np.concatenate([[x[0]], np.diff(x)])
            dy = np.concatenate([[abs(y[1]-y[0])], np.diff(y)])
            dxx, dyy = np.meshgrid(dx, dy)
            DD = footF * dxx * dyy
            binnorm = np.nansum(DD) / np.nansum(foot_rotate_KM)
        
        footKM = foot_rotate_KM * binnorm
        
        # Distance of maximum contribution
        KMXmax = xi / (1 + mu)
        
        # Store results
        FPall[2]['foot'] = footKM
        FPall[2]['Xmax'] = KMXmax
    return