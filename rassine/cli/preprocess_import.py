from __future__ import annotations

import logging
import pickle
from dataclasses import dataclass
from pathlib import Path
from typing import List, Literal, Sequence, Tuple, TypedDict

import configpile as cp
import numpy as np
import tybles as tb
from astropy.io import fits
from filelock import FileLock
from numpy.typing import NDArray
from typing_extensions import Annotated

from ..analysis import grouping
from ..data import absurd_minus_99_9
from ..io import save_pickle
from .data import LoggingLevel, PathPattern, PickleProtocol
from .preprocess_table import IndividualBasicRow
from .util import log_task_name_and_time


class PickledIndividualSpectrum(TypedDict):
    """
    Data format of the pickle files produced by the preprocessing step
    """

    #: Wavelength in Angstroms
    wave: NDArray[np.float64]

    #: Flux in photon count units, must not have NaNs
    flux: NDArray[np.float64]

    #: Error on flux, must not have NaNs
    flux_err: NDArray[np.float64]

    #: Instrument name
    instrument: str

    #: Observation time in mjd
    mjd: np.float64

    #: Observation time in jdb
    jdb: np.float64

    #: Berv
    berv: np.float64

    #: Simultaneous drift in m/s
    lamp_offset: np.float64

    #: Parallax in milliarcseconds
    plx_mas: np.float64

    #: Secular acceleration
    acc_sec: np.float64

    #: np.min(self.wave)
    wave_min: np.float64

    #: np.max(self.wave)
    wave_max: np.float64

    #: Average delta between bins (note that dlambda is now set by a config parameter)
    dwave: np.float64


# TODO: DACE -> metatable
# TODO: remove the mjd patch

# TODO: rename to Imported
@dataclass(frozen=True)
class IndividualImportedRow:
    """Scalar values from individual pickles tabulated for ease of computation"""

    #: Spectrum name without path and extension
    name: str

    #: Instrument name
    instrument: str

    #: Observation date/time in MJD
    mjd: np.float64

    #: Optional RV shift correction in km/s
    model: np.float64

    #: Median value of model (same for all spectra) in km/s
    rv_mean: np.float64

    #: Difference model - rv_mean in km/s
    rv_shift: np.float64

    jdb: np.float64

    berv: np.float64

    #: Document
    #:
    #: Sometimes called lamp
    lamp_offset: np.float64

    plx_mas: np.float64

    acc_sec: np.float64

    wave_min: np.float64

    wave_max: np.float64

    dwave: np.float64

    hole_left: np.float64

    hole_right: np.float64

    @staticmethod
    def schema() -> tb.Schema[IndividualImportedRow]:
        return tb.schema(
            IndividualImportedRow,
            order_columns=True,
            missing_columns="error",
            extra_columns="drop",
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

    prog_ = Path(__file__).stem

    ini_strict_sections_ = [Path(__file__).stem.split("_")[0]]

    #: Input spectrum table
    input_table: Annotated[Path, cp.Param.store(cp.parsers.path_parser, short_flag_name="-I")]

    #: Output spectrum table
    output_table: Annotated[Path, cp.Param.store(cp.parsers.path_parser, short_flag_name="-O")]

    #: Relative path to the folder containing the raw spectra
    input_folder: Annotated[Path, cp.Param.store(cp.parsers.path_parser, short_flag_name="-i")]

    #: Path pattern for output files
    output_pattern: Annotated[
        PathPattern, cp.Param.store(PathPattern.parser(), short_flag_name="-o")
    ]

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
        Literal["HARPS", "CORALIE", "HARPN", "ESPRESSO", "EXPRESS"],
        cp.Param.store(
            cp.Parser.from_choices(["HARPS", "CORALIE", "HARPN", "ESPRESSO", "EXPRESS"]),
            default_value="HARPS",
        ),
    ]

    #: Parallax in mas (no more necessary ?)
    plx_mas: Annotated[
        float,
        cp.Param.store(cp.parsers.float_parser, default_value="0.0"),
    ]

    #: Type of DRS format
    drs_style: Annotated[
        Literal["old", "new"], cp.Param.store(cp.Parser.from_choices(["old", "new"]))
    ]


def find_hole(wave: NDArray[np.float64], flux: NDArray[np.float64]) -> Tuple[float, float]:
    """
    Finds a gap between CCD

    Args:
        wave: Wavelength array
        flux: Flux array

    Returns:
        The endpoints of the wavelength interval if the gap, or (-99.9, -99.9) if not found
    """
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


def preprocess_import(
    row: IndividualBasicRow,
    header: fits.Header,
    data: np.ndarray,
    instrument: Literal["HARPS", "CORALIE", "HARPN", "ESPRESSO", "EXPRESS"],
    plx_mas: float,
    drs_style: Literal["old", "new"],
) -> Tuple[PickledIndividualSpectrum, IndividualImportedRow]:

    if drs_style == "old":
        spectre = data.astype("float64")  # the flux of your spectrum
        spectre_step = np.round(header["CDELT1"], 8)
        spectre_error = np.zeros(len(spectre))
        wave_min = np.round(header["CRVAL1"], 8)  # to round float32
        wave_max = np.round(
            header["CRVAL1"] + (len(spectre) - 1) * spectre_step, 8
        )  # to round float32
        wave = np.round(np.linspace(wave_min, wave_max, len(spectre)), 8)

        # cut left and right parts with zero flux
        # and reevaluate wave_min and wave_max

        # should this be done outside?
        begin = np.min(np.arange(len(spectre))[spectre > 0])
        end = np.max(np.arange(len(spectre))[spectre > 0])
        wave = wave[begin : end + 1]
        spectre = spectre[begin : end + 1]
        kw = "ESO"
        if instrument == "HARPN":
            kw = "TNG"
        berv = np.float64(header[f"HIERARCH {kw} DRS BERV"])
        lamp = np.float64(header[f"HIERARCH {kw} DRS CAL TH LAMP OFFSET"])
    else:
        spectre = data["flux"].astype("float64")  # the flux of your spectrum
        spectre_error = data["error"].astype("float64")  # the flux of your spectrum
        # wave was grid in the previous code
        wave = data["wavelength_air"].astype(
            "float64"
        )  # the grid of wavelength of your spectrum (assumed equidistant in lambda)
        begin = np.min(np.arange(len(spectre))[spectre > 0])  # remove border spectrum with 0 value
        end = np.max(np.arange(len(spectre))[spectre > 0])  # remove border spectrum with 0 value
        wave = wave[begin : end + 1]
        spectre = spectre[begin : end + 1]
        spectre_error = spectre_error[begin : end + 1]
        spectre_step = np.mean(np.diff(wave))
        kw = "ESO"
        if "HIERARCH TNG QC BERV" in header:
            kw = "TNG"
        berv = np.float64(header["HIERARCH " + kw + " QC BERV"])
        lamp = np.float64(0.0)  # header['HIERARCH ESO DRS CAL TH LAMP OFFSET'] no yet available

    wave_min = np.min(wave)
    wave_max = np.max(wave)

    if instrument == "CORALIE":
        if np.mean(spectre) < 100000:
            spectre *= 400780143771.18976  # calibrated with HD8651 2016-12-16 AND 2013-10-24

        spectre /= 1.4e10 / 125**2  # calibrated to match with HARPS SNR

    mjd = row.mjd

    pma = np.float64(0.0)
    pmd = np.float64(0.0)
    if f"HIERARCH {kw} TEL TARG PMA" in header:
        assert f"HIERARCH {kw} TEL TARG PMD" in header
        pma = np.float64(header[f"HIERARCH {kw} TEL TARG PMA"] * 1000)
        pmd = np.float64(header[f"HIERARCH {kw} TEL TARG PMD"] * 1000)

    if plx_mas:
        distance_m = 1000.0 / plx_mas * 3.08567758e16
        mu_radps = (
            np.sqrt(pma**2 + pmd**2) * 2 * np.pi / (360.0 * 1000.0 * 3600.0 * 86400.0 * 365.25)
        )
        acc_sec = distance_m * 86400.0 * mu_radps**2  # rv secular drift in m/s per days
    else:
        acc_sec = 0.0
    jdb = np.float64(mjd) + 0.5

    hole_left, hole_right = find_hole(wave, spectre)
    if hole_left != absurd_minus_99_9 and hole_right != absurd_minus_99_9:
        logging.info(f"Gap detected in s1d between {hole_left:.2f} and {hole_right:.2f}")

    output_pickle: PickledIndividualSpectrum = {
        "wave": wave,
        "flux": spectre,
        "flux_err": spectre_error,
        "instrument": instrument,
        "mjd": mjd,
        "jdb": np.float64(jdb),
        "berv": np.float64(berv),
        "lamp_offset": np.float64(lamp),
        "plx_mas": np.float64(plx_mas),
        "acc_sec": np.float64(acc_sec),
        "wave_min": wave_min,
        "wave_max": wave_max,
        "dwave": spectre_step,
    }

    output_row = IndividualImportedRow(
        name=row.name,
        instrument=instrument,
        mjd=mjd,
        model=row.model,
        rv_mean=row.rv_mean,
        rv_shift=row.rv_shift,
        jdb=jdb,
        berv=berv,
        lamp_offset=lamp,
        plx_mas=np.float64(plx_mas),
        acc_sec=np.float64(acc_sec),
        wave_min=wave_min,
        wave_max=wave_max,
        dwave=spectre_step,
        hole_left=np.float64(hole_left),
        hole_right=np.float64(hole_right),
    )
    return output_pickle, output_row


@log_task_name_and_time(name="preprocess_import")
def run(t: Task) -> None:
    t.logging_level.set()
    t.pickle_protocol.set()
    tyble = IndividualBasicRow.schema().read_csv(t.root / t.input_table, return_type="Tyble")
    inputs: Sequence[int] = t.inputs

    if not inputs:
        inputs = list(range(len(tyble)))
    rows1: List[IndividualImportedRow] = []
    for i in inputs:
        row = tyble[i]
        input_filename = t.root / t.input_folder / row.raw_filename
        output_filename = t.output_pattern.to_path(t.root, row.name)
        data = fits.getdata(input_filename)
        header = fits.getheader(input_filename)
        output_pickle, output_row = preprocess_import(
            row=row,
            header=header,
            data=data,
            instrument=t.instrument,
            plx_mas=t.plx_mas,
            drs_style=t.drs_style,
        )
        rows1.append(output_row)
        save_pickle(output_filename, output_pickle)

    output_table = t.root / t.output_table
    output_table_lockfile = output_table.with_suffix(output_table.suffix + ".lock")

    logging.debug(f"Appending to output table {output_table}")
    with FileLock(output_table_lockfile):
        df = IndividualImportedRow.schema().from_rows(rows1, return_type="DataFrame")
        df.to_csv(output_table, header=not output_table.exists(), mode="a", index=False)


def cli() -> None:
    run(Task.from_command_line_())
