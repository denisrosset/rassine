from __future__ import annotations

import logging
import pickle
from dataclasses import dataclass
from pathlib import Path
from typing import List, Optional, Sequence, Tuple, TypedDict

import configpile as cp
import numpy as np
import numpy.typing as npt
import pandas as pd
import tybles as tb
from astropy.io import fits
from astropy.time import Time
from filelock import FileLock
from numpy.typing import NDArray
from typing_extensions import Annotated

from rassine.math import create_grid

from ..analysis import grouping
from ..data import absurd_minus_99_9
from .data import LoggingLevel, PickleProtocol
from .preprocess_table import Individual
from .util import log_task_name_and_time


class PickledIndividualSpectrum(TypedDict):
    """
    Data format of the pickle files produced by the preprocessing step
    """

    #: TODO: doc
    wave: Optional[npt.NDArray[np.float64]]
    #: TODO: doc, size=spectrum length
    flux: npt.NDArray[np.float64]
    #: TODO: doc, size=spectrum length
    flux_err: npt.NDArray[np.float64]
    #: instrument name
    instrument: str
    #: observation time in mjd
    mjd: np.float64
    #: what is jdb?
    jdb: np.float64
    #: what is berv?
    berv: np.float64
    #: what is lamp offset?
    lamp_offset: np.float64
    #: what is plx_mas?
    plx_mas: np.float64
    #: what is acc_sec?
    acc_sec: np.float64
    #: what is wave_min?
    wave_min: np.float64
    #: what is wave_max?
    wave_max: np.float64
    #: what is dwave?
    dwave: np.float64


# TODO: DACE -> metatable
# TODO: remove the mjd patch
# TODO: check what should be done when FITS are not readable
# TODO: do we need the plx_mas thing?


@dataclass(frozen=True)
class Individual1:
    #: Spectrum name without path and extension
    name: str

    #: Instrument name
    instrument: str

    #: Observation date/time in MJD
    mjd: np.float64

    jdb: np.float64

    berv: np.float64

    lamp_offset: np.float64

    plx_mas: np.float64

    acc_sec: np.float64

    wave_min: np.float64

    wave_max: np.float64

    dwave: np.float64

    hole_left: np.float64

    hole_right: np.float64

    @staticmethod
    def schema() -> tb.Schema[Individual1]:
        return tb.schema(
            Individual1, order_columns=True, missing_columns="error", extra_columns="drop"
        )


# Removed stuff: how to recover mjd from fits / filename
# if mt is not None:
#     mjd = mt.table.loc[mt.table["filename"] == str(file.name), "mjd"].values[0]
# else:
#     try:
#         mjd = header["MJD-OBS"]
#     except KeyError:
#         mjd = Time(file.name.split(".")[1]).mjd


@dataclass(frozen=True)
class Task(cp.Config):
    """Import FITS files into pickle files that can be processed by RASSINE"""

    #
    # Common information
    #

    env_prefix_ = "RASSINE"

    #: Use the specified configuration files.
    #:
    #: Files can be separated by commas/the command can be invoked multiple times.
    config: Annotated[Sequence[Path], cp.Param.config(env_var_name="RASSINE_CONFIG")]

    #: Root path of the data, used as a base for other relative paths
    root: Annotated[Path, cp.Param.store(cp.parsers.path_parser, env_var_name="RASSINE_ROOT")]

    #: Pickle protocol version to use
    pickle_protocol: Annotated[
        PickleProtocol, cp.Param.store(PickleProtocol.parser(), default_value="3")
    ]

    #: Logging level to use
    logging_level: Annotated[
        LoggingLevel,
        cp.Param.store(
            LoggingLevel.parser(), default_value="WARNING", env_var_name="RASSINE_LOGGING_LEVEL"
        ),
    ]

    #
    # Task specific information
    #

    prog_ = "preprocess_import"

    ini_strict_sections_ = ["preprocess"]

    #: Input spectrum table
    input_table: Annotated[Path, cp.Param.store(cp.parsers.path_parser, short_flag_name="-I")]

    #: Output spectrum table
    output_table: Annotated[Path, cp.Param.store(cp.parsers.path_parser, short_flag_name="-O")]

    #: Relative path to the folder containing the raw spectra
    input_folder: Annotated[Path, cp.Param.store(cp.parsers.path_parser, short_flag_name="-i")]

    #: Name of the output directory. If None, the output directory is created at the same location than the spectra.
    output_folder: Annotated[Path, cp.Param.store(cp.parsers.path_parser, short_flag_name="-o")]

    #: Indices of spectrum to process
    #:
    #: If not provided, all spectra are processed
    inputs: Annotated[
        Sequence[int],
        cp.Param.append1(
            cp.parsers.int_parser,
            positional=cp.Positional.ZERO_OR_MORE,
            long_flag_name=None,
            short_flag_name=None,
        ),
    ]

    #: Instrument format of the s1d spectra
    instrument: Annotated[
        str, cp.Param.store(cp.parsers.stripped_str_parser, default_value="HARPS")
    ]

    #: Parallax in mas (no more necessary ?)
    plx_mas: Annotated[
        float,
        cp.Param.store(cp.parsers.float_parser, default_value="0.0"),
    ]

    def validate_output_folder(self) -> Optional[cp.Err]:
        return cp.Err.check(
            (self.root / self.output_folder).is_dir(), "The output directory needs to exist"
        )


def find_hole(wave: NDArray[np.float64], flux: NDArray[np.float64]) -> Tuple[float, float]:
    null_flux = np.where(flux == 0)[0]  # criterion to detect gap between ccd
    left = absurd_minus_99_9
    right = absurd_minus_99_9
    if len(null_flux) > 0:
        mask = grouping(np.diff(null_flux), 0.5, 0)[-1]
        highest = mask[mask[:, 2].argmax()]
        if highest[2] > 1000:
            left = wave[int(null_flux[highest[0]])]  # store left or -99
            right = wave[int(null_flux[highest[1]])]  # store right or -99
    return (left, right)


def preprocess_fits_harps_coraline_harpn(t: Task, row: Individual) -> Individual1:
    """Preprocess one spectrum coming from the HARPS/CORALINE/HARPN instrument"""
    logging.info(f"Processing spectrum {row.name}")

    instrument = t.instrument
    plx_mas = t.plx_mas

    input_file = t.root / t.input_folder / row.raw_filename
    output_file = t.root / t.output_folder / (row.name + ".p")

    logging.debug(f"Reading FITS file {input_file}")
    # Load the FITS file
    header = fits.getheader(input_file)  # load the fits hefluxcannot be read it errors
    data = fits.getdata(input_file)
    spectre = data.astype("float64")  # the flux of your spectrum
    spectre_step = np.round(header["CDELT1"], 8)
    wave_min = np.round(header["CRVAL1"], 8)  # to round float32
    wave_max = np.round(
        header["CRVAL1"] + (len(spectre) - 1) * spectre_step, 8
    )  # to round float32

    grid = np.round(np.linspace(wave_min, wave_max, len(spectre)), 8)

    begin = np.min(np.arange(len(spectre))[spectre > 0])
    end = np.max(np.arange(len(spectre))[spectre > 0])
    grid = grid[begin : end + 1]
    spectre = spectre[begin : end + 1]
    wave_min = np.min(grid)
    wave_max = np.max(grid)

    kw = "ESO"
    if instrument == "HARPN":
        kw = "TNG"

    berv = header["HIERARCH " + kw + " DRS BERV"]
    lamp = header["HIERARCH " + kw + " DRS CAL TH LAMP OFFSET"]
    try:
        pma = header["HIERARCH " + kw + " TEL TARG PMA"] * 1000
        pmd = header["HIERARCH " + kw + " TEL TARG PMD"] * 1000
    except:
        pma = 0
        pmd = 0

    if plx_mas:
        distance_m = 1000.0 / plx_mas * 3.08567758e16
        mu_radps = (
            np.sqrt(pma**2 + pmd**2) * 2 * np.pi / (360.0 * 1000.0 * 3600.0 * 86400.0 * 365.25)
        )
        acc_sec = distance_m * 86400.0 * mu_radps**2  # rv secular drift in m/s per days
    else:
        acc_sec = 0

    if instrument == "CORALIE":
        if np.mean(spectre) < 100000:
            spectre *= 400780143771.18976  # calibrated with HD8651 2016-12-16 AND 2013-10-24

        spectre /= 1.4e10 / 125**2  # calibrated to match with HARPS SNR

    mjd: np.float64 = row.mjd

    jdb = np.float64(mjd + 0.5)

    # should we reuse grid?
    wave = create_grid(wave_min, spectre_step, len(spectre))  # dwave=spectre_step, flux=spectre
    hole_left, hole_right = find_hole(wave, spectre)

    out: PickledIndividualSpectrum = {
        "wave": None,
        "flux": spectre,
        "flux_err": np.zeros(len(spectre)),
        "instrument": instrument,
        "mjd": mjd,
        "jdb": jdb,
        "berv": berv,
        "lamp_offset": lamp,
        "plx_mas": np.float64(plx_mas),
        "acc_sec": acc_sec,
        "wave_min": wave_min,
        "wave_max": wave_max,
        "dwave": spectre_step,
    }

    logging.debug(f"Writing pickle file {output_file}")
    with open(output_file, "wb") as f:
        pickle.dump(out, f, t.pickle_protocol.level)

    return Individual1(
        name=row.name,
        instrument=instrument,
        mjd=mjd,
        jdb=jdb,
        berv=berv,
        lamp_offset=lamp,
        plx_mas=np.float64(plx_mas),
        acc_sec=acc_sec,
        wave_min=wave_min,
        wave_max=wave_max,
        dwave=spectre_step,
        hole_left=np.float64(hole_left),
        hole_right=np.float64(hole_right),
    )


# def preprocess_fits_espresso_express(t: Task, row: BasicInfo) -> None:
#     instrument = t.instrument
#     plx_mas = t.plx_mas
#     name = row.filename
#     file: Path = t.root.at(t.input_folder, name)
#     output_file: Path = t.root.at(t.output_folder, file.stem + ".p")

#     header = fits.getheader(file)  # load the fits header
#     data = fits.getdata(file)
#     spectre = data["flux"].astype("float64")  # the flux of your spectrum
#     spectre_error = data["error"].astype("float64")  # the flux of your spectrum
#     grid = data["wavelength_air"].astype(
#         "float64"
#     )  # the grid of wavelength of your spectrum (assumed equidistant in lambda)
#     begin = np.min(np.arange(len(spectre))[spectre > 0])  # remove border spectrum with 0 value
#     end = np.max(np.arange(len(spectre))[spectre > 0])  # remove border spectrum with 0 value
#     grid = grid[begin : end + 1]
#     spectre = spectre[begin : end + 1]
#     spectre_error = spectre_error[begin : end + 1]
#     wave_min = np.min(grid)
#     wave_max = np.max(grid)
#     spectre_step = np.mean(np.diff(grid))
#     mjd = row.mjd

#     kw = "ESO"
#     if "HIERARCH TNG QC BERV" in header:
#         kw = "TNG"

#     berv = float(header["HIERARCH " + kw + " QC BERV"])
#     lamp = 0  # header['HIERARCH ESO DRS CAL TH LAMP OFFSET'] no yet available
#     try:
#         pma = header["HIERARCH " + kw + " TEL TARG PMA"] * 1000
#         pmd = header["HIERARCH " + kw + " TEL TARG PMD"] * 1000
#     except:
#         pma = 0
#         pmd = 0

#     if plx_mas:
#         distance_m = 1000.0 / plx_mas * 3.08567758e16
#         mu_radps = (
#             np.sqrt(pma**2 + pmd**2) * 2 * np.pi / (360.0 * 1000.0 * 3600.0 * 86400.0 * 365.25)
#         )
#         acc_sec = distance_m * 86400.0 * mu_radps**2  # rv secular drift in m/s per days
#     else:
#         acc_sec = 0
#     jdb = np.array(mjd) + 0.5

#     out: OutputDict = {
#         "wave": grid,
#         "flux": spectre,
#         "flux_err": spectre_error,
#         "instrument": instrument,
#         "mjd": mjd,
#         "jdb": np.float64(jdb),
#         "berv": np.float64(berv),
#         "lamp_offset": np.float64(lamp),
#         "plx_mas": np.float64(plx_mas),
#         "acc_sec": acc_sec,
#         "wave_min": wave_min,
#         "wave_max": wave_max,
#         "dwave": spectre_step,
#     }
#     save(output_file, out, t.pickle_protocol)


@log_task_name_and_time(name="preprocess_import")
def run(t: Task) -> None:
    t.logging_level.set()
    t.pickle_protocol.set()
    tyble = Individual.schema().read_csv(t.root / t.input_table, return_type="Tyble")
    inputs: Sequence[int] = t.inputs

    if not inputs:
        inputs = list(range(len(tyble)))
    rows1: List[Individual1] = []
    for i in inputs:
        r = tyble[i]
        if t.instrument in ["ESPRESSO", "EXPRESS"]:
            raise NotImplementedError
            # preprocess_fits_espresso_express(t, r)
        elif t.instrument in ["HARPS", "CORALIE", "HARPN"]:
            rows1.append(preprocess_fits_harps_coraline_harpn(t, r))
        else:
            raise ValueError(f"Instrument {t.instrument} not implemented")

    output_table = t.root / t.output_table
    output_table_lockfile = output_table.with_suffix(output_table.suffix + ".lock")

    logging.debug(f"Appending to output table {output_table}")
    with FileLock(output_table_lockfile):
        df = Individual1.schema().from_rows(rows1, return_type="DataFrame")
        df.to_csv(output_table, header=not output_table.exists(), mode="a", index=False)


def cli() -> None:
    run(Task.from_command_line_())
