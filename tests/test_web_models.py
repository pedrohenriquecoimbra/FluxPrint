"""The web page's list of models: what the wheel it runs registers, with no edit in web/."""
import importlib
import importlib.util
import json
import logging
import subprocess
import sys
import types
import zipfile
from pathlib import Path

import numpy as np
import pytest

import fluxprint
from fluxprint.model import MODELS, footprint_model, get_model

ROOT = Path(__file__).parents[1]
BUILD = ROOT / "scripts" / "build_site.py"
if not BUILD.exists():    # publish.yml tests the built wheel with a copy of tests/ alone
    pytest.skip("the web page is not next to these tests", allow_module_level=True)

spec = importlib.util.spec_from_file_location("build_site", BUILD)
build_site = importlib.util.module_from_spec(spec)
spec.loader.exec_module(build_site)

# What the page showed for its two models when it held them by hand, citations as checked on Crossref.
PAGE = {
    "kljun2015": dict(
        name="Kljun et al. 2015", reach=75, needs_z0=False, rslayer=0,
        citations=["Kljun, N., Calanca, P., Rotach, M. W., Schmid, H. P. (2015). A simple two-dimensional parameterisation for Flux Footprint Prediction (FFP). Geosci. Model Dev. 8, 3695-3713. https://doi.org/10.5194/gmd-8-3695-2015"]),
    "hsieh2000": dict(
        name="Hsieh et al. 2000", reach=200, needs_z0=True, rslayer=1,
        citations=[
            "Hsieh, C.-I., Katul, G., Chi, T. (2000). An approximate analytical model for footprint estimation of scalar fluxes in thermally stratified atmospheric flows. Adv. Water Resour. 23, 765-772. https://doi.org/10.1016/S0309-1708(99)00042-1",
            "Detto, M., Montaldo, N., Albertson, J. D., Mancini, M., Katul, G. (2006). Soil moisture and vegetation controls on evapotranspiration in a heterogeneous Mediterranean ecosystem on Sardinia, Italy. Water Resour. Res. 42, W08419. https://doi.org/10.1029/2005WR004693"]),
}

# What a wheel that predates reach and needs_z0 answers, in the order available_models() gives. This one is 0.4.0,
# which also has the title of Kljun et al. (2015) wrong.
OLDER = {"version": "0.4.0", "models": {
    "hsieh2000": dict(
        _description="Hsieh et al. (2000) analytical footprint with Detto et al. (2006) crosswind expansion",
        model_citation="Hsieh, C.-I., G. Katul, T. Chi (2000): An approximate analytical model for footprint estimation of scalar fluxes in thermally stratified atmospheric flows. Adv. Water Resour. 23, 765-772.",
        model_doi="10.1016/S0309-1708(99)00042-1",
        lateral_citation="Detto, M., N. Montaldo, J.D. Albertson, M. Mancini, G. Katul (2006), Water Resour. Res. 42, W08419, Appendix B (crosswind dispersion).",
        reach=None, needs_z0=None),
    "kljun2015": dict(
        _description="Kljun et al. (2015) FFP parameterisation",
        model_citation="Kljun, N., P. Calanca, M.W. Rotach, H.P. Schmid (2015): The simple two-dimensional parameterisation for Flux Footprint Prediction (FFP). Geosci. Model Dev. 8, 3695-3713.",
        model_doi="10.5194/gmd-8-3695-2015", lateral_citation=None, reach=None, needs_z0=None),
}}

DECLARED_MODEL = '''
import numpy as np
from .engine import footprint_model

@footprint_model("new2026", description="New and Newer (2026) test footprint", reach=120, needs_z0=True,
                 meta={"model_citation": "New, A., Newer, B. (2026): A test footprint. J. Tests 1, 1-2.",
                       "model_doi": "10.0000/new", "lateral_citation": "Side, C. (2025), J. Tests 0, 3."})
def kernel(ctx, rec, opts):
    return np.ones(ctx.x_2d.shape), 0, 1
'''

BARE_MODEL = '''
import numpy as np
from .engine import footprint_model

@footprint_model("bare2026")
def kernel(ctx, rec, opts):
    return np.ones(ctx.x_2d.shape), 0, 1
'''


def release(folder, *added):
    """A wheel of this fluxprint with more modules in fluxprint.model, as version 9.9.9."""
    package = Path(fluxprint.__file__).parent
    wheel = folder / "fluxprint-9.9.9-py3-none-any.whl"
    with zipfile.ZipFile(wheel, "w") as archive:
        for path in package.rglob("*.py"):
            name = path.relative_to(package.parent).as_posix()
            content = path.read_bytes()
            if name == "fluxprint/version.py":
                content = content.replace(fluxprint.__version__.encode(), b"9.9.9")
            if name == "fluxprint/model/__init__.py":
                content += "".join(f"from . import added_{i}\n" for i in range(len(added))).encode()
            archive.writestr(name, content)
        for i, source in enumerate(added):
            archive.writestr(f"fluxprint/model/added_{i}.py", source)
    return wheel


def test_a_released_model_reaches_the_page(tmp_path, monkeypatch):
    (tmp_path / "wheels").mkdir()
    (tmp_path / "data").mkdir()
    release(tmp_path / "wheels", DECLARED_MODEL, BARE_MODEL)
    monkeypatch.setattr(build_site, "OUT", tmp_path)
    build_site.write_models()
    listed = json.loads((tmp_path / "data" / "models.json").read_text(encoding="utf-8"))
    assert listed["fluxprint"] == "9.9.9"   # the wheel answered, not the fluxprint running these tests
    assert list(listed["models"]) == ["kljun2015", "hsieh2000", "bare2026", "new2026"]
    assert listed["models"]["new2026"] == dict(
        name="New and Newer (2026) test footprint", reach=120, needs_z0=True, rslayer=0,
        citations=["New, A., Newer, B. (2026): A test footprint. J. Tests 1, 1-2. https://doi.org/10.0000/new",
                   "Side, C. (2025), J. Tests 0, 3."])


def test_a_model_that_declares_nothing_still_gets_a_usable_entry(tmp_path):
    registry = build_site.read_registry([release(tmp_path, BARE_MODEL)])
    assert build_site.page_models(registry)["models"]["bare2026"] == dict(
        name="bare2026", reach=build_site.DEFAULT_REACH, needs_z0=False, rslayer=0, citations=[])


def test_a_wheel_without_the_metadata_keeps_the_page_as_it_was():
    listed = build_site.page_models(OLDER)
    assert listed["fluxprint"] == "0.4.0"
    assert list(listed["models"]) == ["kljun2015", "hsieh2000"]     # the first is the form's default
    assert listed["models"] == PAGE


def test_a_wheel_with_the_metadata_keeps_the_page_as_it_was(tmp_path):
    listed = build_site.page_models(build_site.read_registry([release(tmp_path)]))
    assert list(listed["models"]) == ["kljun2015", "hsieh2000"]
    assert listed["models"] == PAGE


def test_what_a_wheel_declares_comes_before_the_page():
    declared = dict(OLDER["models"]["hsieh2000"], reach=320, needs_z0=False, model_citation="Another text.")
    listed = build_site.page_models({"version": "1.0", "models": {"hsieh2000": declared}})["models"]["hsieh2000"]
    assert listed == dict(PAGE["hsieh2000"], reach=320, needs_z0=False)     # name and citations stay the page's


def test_a_model_the_wheel_lacks_is_not_offered():
    registry = {"version": "1.0", "models": {"kljun2015": OLDER["models"]["kljun2015"]}}
    assert list(build_site.page_models(registry)["models"]) == ["kljun2015"]


def test_a_wheel_that_cannot_be_read_stops_the_build(tmp_path):
    with pytest.raises(subprocess.CalledProcessError):
        build_site.read_registry([release(tmp_path, "raise ImportError('a broken release')\n")])


def test_the_metadata_agrees_with_what_the_page_keeps():
    for name, own in build_site.PAGE_MODELS.items():
        assert get_model(name).reach == own["reach"]
        assert get_model(name).needs_z0 == own.get("needs_z0", False)


@pytest.mark.parametrize("path", sorted((ROOT / "web").glob("*.*")), ids=lambda path: path.name)
def test_the_page_names_no_model(path):
    text = path.read_text(encoding="utf-8")
    assert not any(model in text for model in PAGE)


@pytest.fixture
def tool(monkeypatch):
    """web/tool.py outside the browser, with its one browser import stubbed."""
    logger = logging.getLogger("fluxprint")
    handlers, level = list(logger.handlers), logger.level
    monkeypatch.setitem(sys.modules, "js", types.SimpleNamespace(fpProgress=lambda done, total: None))
    monkeypatch.syspath_prepend(str(ROOT / "web"))
    yield importlib.import_module("tool")
    logger.handlers[:] = handlers
    logger.setLevel(level)
    for name in ("tool", "fluxnet"):
        sys.modules.pop(name, None)


def run_tool(tool, monkeypatch, row, needs_z0, rslayer):
    """What tool.run says for one half-hour with a model that records what reaches it, and that record."""
    received = []

    @footprint_model("seen2026", options=("rslayer",), defaults={"rslayer": 0})
    def kernel(ctx, rec, opts):
        received.append(dict(z0=rec.z0, umean=rec.umean, rslayer=opts["rslayer"]))
        return np.zeros(ctx.x_2d.shape), 3, 0   # no footprint, so tool.run stops before writing one

    monkeypatch.setattr(np, "fromfile", lambda path, dtype: np.array(row, dtype="<f4"))
    options = dict(half=100, model="seen2026", needs_z0=needs_z0, rslayer=rslayer, lat=48.0, lon=2.0, provenance={})
    try:
        return json.loads(tool.run(json.dumps(options))), received
    finally:
        MODELS.pop("seen2026", None)


# zm, umean, ustar, pblh, mo_length, v_sigma, wind_dir
ABOVE_THE_SUBLAYER = [20.0, 6.0, 0.3, 1000.0, -100.0, 0.5, 180.0]
IN_THE_SUBLAYER = [20.0, 2.0, 0.5, 1000.0, -100.0, 0.5, 180.0]
Z0_ABOVE_ZM = [20.0, 1.0, 0.5, 1000.0, 10.0, 0.5, 180.0]


@pytest.mark.parametrize("needs_z0", [False, True])
def test_tool_gives_a_model_the_wind_input_it_declares(tool, monkeypatch, needs_z0):
    summary, [record] = run_tool(tool, monkeypatch, ABOVE_THE_SUBLAYER, needs_z0, rslayer=0)
    assert summary["model"] == "seen2026"
    assert (record["z0"] is not None, record["umean"] is None) == (needs_z0, needs_z0)
    assert ("z0" in summary) == ("z0" in tool.provenance) == needs_z0


def test_tool_runs_in_the_sublayer_only_when_the_list_says_so(tool, monkeypatch):
    summary, received = run_tool(tool, monkeypatch, IN_THE_SUBLAYER, True, rslayer=0)
    assert not received     # the checks rejected it
    assert "sublayer" not in summary and "roughness_sublayer" not in tool.provenance
    summary, [record] = run_tool(tool, monkeypatch, IN_THE_SUBLAYER, True, rslayer=1)
    assert record["rslayer"] == 1
    assert summary["sublayer"] == 1 and "roughness_sublayer" in tool.provenance


def test_tool_blames_z0_above_zm_only_when_the_checks_are_lifted(tool, monkeypatch):
    run_tool(tool, monkeypatch, Z0_ABOVE_ZM, True, rslayer=0)
    assert tool.NO_FOOTPRINT not in tool.rejections     # the checks already gave their reason
    summary, _ = run_tool(tool, monkeypatch, Z0_ABOVE_ZM, True, rslayer=1)
    assert summary["reason"] == tool.NO_FOOTPRINT
