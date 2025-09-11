import numpy as np


def caller(data, variable):
    fc = {
        "z0": compute_z0(np.array(data['umean']), np.array(data['ustar']), np.array(data['zm']), ol=np.array(data.get('mo_length', 1))),
        "mo_length": 1,  # compute_mo_length(data['ustar'], data['lat']),
        "pblh": 1000,  # compute_pblh(),
        "v_sigma": compute_std_v(np.array(data['ustar'])),
    }
    return fc.get(variable, None)


def compute_z0(umean, ustar, zm, psi_f=None, ol=None, k=0.4):
    """
    Not yet tested
    From Kljun.py"""
    k = 0.4  # von Karman
    if not psi_f:
        psi_f = compute_psi_f(zm, ol)
    exponent = (umean / ustar) * k + psi_f
    z0 = zm / np.exp(exponent)
    return z0


def compute_psi_f(zm, ol):
    """
    Not yet tested
    From Kljun.py"""
    oln = 5000  # limit to L for neutral scaling
    # Use z0
    if ol <= 0 or ol >= oln:
        xx = (1 - 19.0 * zm/ol)**0.25
        psi_f = np.log((1 + xx**2) / 2.) + 2. * \
                        np.log((1 + xx) / 2.) - 2. * np.arctan(xx) + np.pi/2
    elif ol > 0 and ol < oln:
        psi_f = -5.3 * zm / ol
    return psi_f


def compute_pblh(ustar, latitude_deg):
    """Not yet tested"""
    # h = Boundary layer height[m]
    omega = 7.2921e-5  # Earth's angular velocity in rad/s
    phi = np.radians(latitude_deg)
    f = 2 * omega * np.sin(phi)
    return ustar / f


def compute_mo_length(ustar, theta, heat_flux, k=0.4, g=9.81):
    """Not yet tested"""
    # ol = Obukhov length[m]
    return - (ustar ** 3) * theta / (k * g * heat_flux)


def compute_std_v(ustar, a=2.0):
    """Not yet tested"""
    # sigmav = standard deviation of lateral velocity fluctuations[ms-1]
    return a * ustar


def compute_ustar(umean, zm, z0, k=0.4):
    """Not yet tested"""
    # ustar = friction velocity[ms-1]
    return (umean * k) / np.log(zm / z0)



