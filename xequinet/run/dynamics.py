import argparse
from typing import Any, Dict, Optional, Tuple, cast

import numpy as np
import torch
from ase import Atoms, optimize, units
from ase.io import Trajectory
from ase.io import read as ase_read
from ase.io import write as ase_write
from ase.md import andersen, langevin, md, npt, nptberendsen, nvtberendsen, verlet
from ase.md.velocitydistribution import (
    MaxwellBoltzmannDistribution,
    Stationary,
    ZeroRotation,
)
from omegaconf import OmegaConf

from xequinet.interface import XequiCalculator
from xequinet.utils import MDConfig


def resolve_ensemble(
    atoms: Atoms,
    ensemble: Dict[str, Any],
    logfile: str,
    trajectory: Optional[str] = None,
) -> Tuple[md.MolecularDynamics, Optional[int], Optional[int]]:
    ensemble_factory = {
        "VelocityVerlet": verlet.VelocityVerlet,
        "Langevin": langevin.Langevin,
        "Andersen": andersen.Andersen,
        "NVTBerendsen": nvtberendsen.NVTBerendsen,
        "NPTBerendsen": nptberendsen.NPTBerendsen,
        "NPT": npt.NPT,
    }
    ensemble_name = ensemble.pop("name")
    if ensemble_name in ensemble_factory:
        ensemble_cls = ensemble_factory[ensemble_name]
    elif hasattr(optimize, ensemble_name):
        ensemble_cls = getattr(optimize, ensemble_name)
    else:
        raise ValueError(f"Unknown ensemble: {ensemble_name}")

    # adjust some units
    # time related units
    time_args = ["timestep", "ttime", "taut", "taup"]
    for t_unit in time_args:
        if t_unit in ensemble:
            ensemble[t_unit] *= units.fs
    if "friction" in ensemble:
        ensemble["friction"] /= units.fs
    # pressure related units
    if "externalstress" in ensemble:
        ensemble["externalstress"] *= units.GPa
    if "pressure" in ensemble:
        ensemble["pressure"] *= units.GPa
    if "pfactor" in ensemble:
        ensemble["pfactor"] *= units.GPa * units.fs**2

    steps = ensemble.pop("steps", None)
    fmax = ensemble.pop("fmax", None)
    dyn = ensemble_cls(
        atoms=atoms,
        logfile=logfile,
        trajectory=trajectory,
        **ensemble,
    )
    return dyn, steps, fmax


def traj2xyz(
    trajectory: str, traj_xyz: str, columns: list = ["symbols", "positions"]
) -> None:
    """
    Convert trajectory file to extend xyz file.
    """
    with open(traj_xyz, "w"):
        pass
    for atoms in Trajectory(trajectory):
        ase_write(
            filename=traj_xyz,
            images=atoms,
            format="extxyz",
            append=True,
            write_results=False,
            columns=columns,
        )


def run_md(args: argparse.Namespace) -> None:
    # load md config
    if args.config is None:
        raise ValueError("Config file is required.")
    config = OmegaConf.merge(
        OmegaConf.structured(MDConfig),
        OmegaConf.load(args.config),
    )
    # this will do nothing, only for type annotation
    config = cast(MDConfig, config)

    # set random seed
    if config.seed is not None:
        np.random.seed(config.seed)
        torch.manual_seed(config.seed)

    # load atoms
    atoms = ase_read(config.input_file, index=0)

    # set calculator
    calc = XequiCalculator(
        ckpt_file=config.model_file,
    )
    atoms.set_calculator(calc)

    # set starting tempeature
    MaxwellBoltzmannDistribution(atoms, temperature_K=config.init_temperature)
    ZeroRotation(atoms)
    Stationary(atoms)

    # initialize log file
    if not config.append_logfile:
        with open(config.logfile, "w"):
            pass

    # set ensemble
    ensembles = []
    for ensemble in config.ensembles:
        ensembles.append(
            resolve_ensemble(atoms, ensemble, config.logfile, config.trajectory)
        )
    # run dynamics
    for (dyn, steps, fmax) in ensembles:
        if fmax is None:
            dyn.run(steps)
        else:
            dyn.run(fmax=fmax)

    # convert trajectory to xyz
    if config.xyz_traj is not None:
        traj2xyz(config.trajectory, config.xyz_traj, config.columns)
