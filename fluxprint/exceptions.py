"""
Exceptions

For original code see  Kljun, N., P. Calanca, M.W. Rotach, H.P. Schmid, 2015: 
The simple two-dimensional parameterisation for Flux Footprint Predictions FFP.
Geosci. Model Dev. 8, 3695-3713, doi:10.5194/gmd-8-3695-2015, for details.
contact: natascha.kljun@cec.lu.se
"""

exTypes = {'message': 'Message',
           'alert': 'Alert',
           'error': 'Error',
           'fatal': 'Fatal error'}

exceptions = [
    {'code': 1,
     'type': exTypes['fatal'],
     'msg': 'At least one required parameter is missing. Please enter all '
            'required inputs. Check documentation for details.'},
    {'code': 2,
     'type': exTypes['error'],
     'msg': 'zm (measurement height) must be larger than zero.'},
    {'code': 3,
     'type': exTypes['error'],
     'msg': 'z0 (roughness length) must be larger than zero.'},
    {'code': 4,
     'type': exTypes['error'],
     'msg': 'h (BPL height) must be larger than 10 m.'},
    {'code': 5,
     'type': exTypes['error'],
     'msg': 'zm (measurement height) must be smaller than h (PBL height).'},
    {'code': 6,
     'type': exTypes['alert'],
     'msg': 'zm (measurement height) should be above roughness sub-layer (12.5*z0).'},
    {'code': 7,
     'type': exTypes['error'],
     'msg': 'zm/ol (measurement height to Obukhov length ratio) must be equal or larger than -15.5.'},
    {'code': 8,
     'type': exTypes['error'],
     'msg': 'sigmav (standard deviation of crosswind) must be larger than zero.'},
    {'code': 9,
     'type': exTypes['error'],
     'msg': 'ustar (friction velocity) must be >=0.1.'},
    {'code': 10,
     'type': exTypes['error'],
     'msg': 'wind_dir (wind direction) must be >=0 and <=360.'},
    {'code': 11,
     'type': exTypes['fatal'],
     'msg': 'Passed data arrays (ustar, zm, h, ol) don\'t all have the same length.'},
    {'code': 12,
     'type': exTypes['fatal'],
     'msg': 'No valid zm (measurement height above displacement height) passed.'},
    {'code': 13,
     'type': exTypes['alert'],
     'msg': 'Using z0, ignoring umean if passed.'},
    {'code': 14,
     'type': exTypes['alert'],
     'msg': 'No valid z0 passed, using umean.'},
    {'code': 15,
     'type': exTypes['fatal'],
     'msg': 'No valid z0 or umean array passed.'},
    {'code': 16,
     'type': exTypes['error'],
     'msg': 'At least one required input is invalid. Skipping current footprint.'},
    {'code': 17,
     'type': exTypes['alert'],
     'msg': 'Only one value of zm passed. Using it for all footprints.'},
    {'code': 18,
     'type': exTypes['fatal'],
     'msg': 'if provided, rs must be in the form of a number or a list of numbers.'},
    {'code': 19,
     'type': exTypes['alert'],
     'msg': 'rs value(s) larger than 90% were found and eliminated.'},
    {'code': 20,
     'type': exTypes['error'],
     'msg': 'zm (measurement height) must be above roughness sub-layer (12.5*z0).'},
]


def check_ffp_inputs(ustar, sigmav, h, ol, wind_dir, zm, z0, umean, rslayer, verbosity):
    # Check passed values for physical plausibility and consistency
    if zm <= 0.:
        raise_ffp_exception(2, verbosity)
        return False
    if z0 is not None and umean is None and z0 <= 0.:
        raise_ffp_exception(3, verbosity)
        return False
    if h <= 10.:
        raise_ffp_exception(4, verbosity)
        return False
    if zm > h :
        raise_ffp_exception(5, verbosity)
        return False
    if z0 is not None and umean is None and zm <= 12.5*z0:
        if rslayer is 1:
            raise_ffp_exception(6, verbosity)
        else:
            raise_ffp_exception(20, verbosity)
            return False
    if float(zm)/ol <= -15.5:
        raise_ffp_exception(7, verbosity)
        return False
    if sigmav <= 0:
        raise_ffp_exception(8, verbosity)
        return False
    if ustar <= 0.1:
        raise_ffp_exception(9, verbosity)
        return False
    if wind_dir > 360:
        raise_ffp_exception(10, verbosity)
        return False
    if wind_dir < 0:
        raise_ffp_exception(10, verbosity)
        return False
    return True


def raise_ffp_exception(code, verbosity=1):
    '''Raise exception or prints message according to specified code'''

    ex = [it for it in exceptions if it['code'] == code][0]
    string = ex['type'] + '(' + str(ex['code']).zfill(4) + '):\n ' + ex['msg']

    if verbosity > 0:
        print('')

    if ex['type'] == exTypes['fatal']:
        if verbosity > 0:
            string = string + '\n FFP_fixed_domain execution aborted.'
        else:
            string = ''
        raise Exception(string)
    elif ex['type'] == exTypes['alert']:
        string = string + '\n Execution continues.'
        if verbosity > 1:
            print(string)
    elif ex['type'] == exTypes['error']:
        string = string + '\n Execution continues.'
        if verbosity > 1:
            print(string)
    else:
        if verbosity > 1:
            print (string)