"""Board integration API. No camera, UART, SSH or training is started on import."""
from .types import Signal, Target, Options, Capabilities, Frame, Command, Decision
from .geometry import Calibration, prepare_image
from .controller import Controller

__all__ = ['Controller', 'Signal', 'Target', 'Options', 'Capabilities', 'Frame',
           'Command', 'Decision', 'Calibration', 'prepare_image']
__version__ = '1.0.0'
