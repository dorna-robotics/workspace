# workspace/components/calibration.py

class Calibration:
    """
    Handles calibration data and interpolation for components.
    Each calibration file (JSON) contains mapping between
    raw joint values and corrected joint values.
    """

    def __init__(self, name):
        self.name = name
        self.data = {}