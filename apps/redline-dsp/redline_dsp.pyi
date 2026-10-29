import numpy as np
import numpy.typing as npt

Plan = tuple[list[float], int, int, int, int]

def oversampled_clip(
    samples: npt.NDArray[np.float32],
    gain: float,
    ceiling: float,
    up: Plan,
    down: Plan,
    sos: list[list[float]],
    zi: list[list[float]],
    edge: int,
    passes: int,
) -> npt.NDArray[np.float32]: ...
def integrated_loudness(
    samples: npt.NDArray[np.float32],
    rate: float,
    stages: list[tuple[list[float], list[float], float]],
    block: float,
    overlap: float,
) -> float: ...
def resample(samples: npt.NDArray[np.float32], plan: Plan) -> npt.NDArray[np.float32]: ...
def sosfilt(samples: npt.NDArray[np.float32], sos: list[list[float]]) -> npt.NDArray[np.float32]: ...
