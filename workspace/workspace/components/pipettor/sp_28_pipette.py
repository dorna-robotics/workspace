# =================================================================
# RASPBERRY PI SETUP INSTRUCTIONS
# =================================================================
# 1. Install PySerial:
#    pip install pyserial
#
# 2. Identify your Port:
#    On a Pi, the USB adapter is usually '/dev/ttyUSB0' or '/dev/ttyACM0'
#    instead of 'COM4'. Use 'ls /dev/tty*' in the terminal to find it.
# =================================================================

import serial
import serial.tools.list_ports
import time

class WrapperSP28Pipettor:
    def __init__(self, port='/dev/ttyUSB0', baud=38400):
        self.port = port
        self.baud = baud
        self.ser = None
        # Settings
        self.liquid_steps_per_ul = 100  # 10000 steps = 100ul

    def connect(self, auto=True) -> bool:
        """Connects to device. Returns True only if device responds."""
        if auto:
            return self._auto_connect()
        try:
            self.ser = serial.Serial(self.port, self.baud, timeout=2)
            response = self._private_send("1>?")
            if "1<" in response:
                return True
            else:
                return False
        except:
            return False

    # auto_connect
    def connect(self) -> bool:
        """
        Loops over all available ports and handshakes with the pipettor.
        Returns True if found, False if not.
        """
        # Get all available serial ports on the system
        ports = list(serial.tools.list_ports.comports())
        
        for p in ports:
            try:
                # Try to open the port
                test_ser = serial.Serial(p.device, self.baud, timeout=0.5)
                
                # Send the Query command: 1>? followed by Carriage Return
                test_ser.write(b"1>?\r")
                time.sleep(0.1)
                
                # Check the response
                response = test_ser.readline().decode('ascii').strip()
                test_ser.close()

                # If it responds with '1<', this is the SP28
                if "1<" in response:
                    self.port = p.device
                    self.ser = serial.Serial(self.port, self.baud, timeout=2)
                    return True
            except:
                # Port busy or no response, move to next
                continue
                
        return False


    def _private_send(self, cmd) -> str:
        """Internal helper for raw communication."""
        if not self.ser: 
            return ""
        try:
            self.ser.write(f"{cmd}\r".encode('ascii'))
            time.sleep(0.15) 
            return self.ser.readline().decode('ascii').strip()
        except:
            return ""

    def _wait_until_idle(self, timeout=15) -> bool:
        """Internal helper to poll status. Returns True when motor stops."""
        start = time.time()
        while time.time() - start < timeout:
            response = self._private_send("1>?")
            if "1<0" in response:
                return True
            time.sleep(0.3)
        return False

    def has_tip(self) -> bool:
        """Explicit Boolean: Returns True if tip is detected, else False."""
        response = self._private_send("1>Rr3")
        if "12:1" in response:
            return True
        else:
            return False

    # speed: microseptes/s
    def initialize(self, speed=16000, eject_tip=True) -> bool:
        """Explicit Boolean: Homes device. Returns True if finished."""
        mode = 0 if eject_tip else 1
        res = self._private_send(f"1>It{speed},100,{mode}")
        if "1<2" in res:
            return self._wait_until_idle()
        else:
            return False

    # speed: ul/s
    # liquid_steps_per_ul = 100  # 10000 steps = 100ul
    def aspirate(self, volume_ul, speed=200, liquid_steps_per_ul=100) -> bool:
        """Explicit Boolean: Pulls liquid. Returns True if finished."""
        steps = int(volume_ul * liquid_steps_per_ul)
        res = self._private_send(f"1>Ia{steps},{speed},10")
        if "1<2" in res:
            return self._wait_until_idle()
        else:
            return False

    # speed: ul/s
    # liquid_steps_per_ul = 100  # 10000 steps = 100ul
    def dispense(self, volume_ul, speed=500, blowout=False, liquid_steps_per_ul=100) -> bool:
        """Explicit Boolean: Pushes liquid. Returns True if finished."""
        steps = int(volume_ul * liquid_steps_per_ul)
        stop_spd = 500 if blowout else 10
        res = self._private_send(f"1>Da{steps},0,{speed},{stop_spd}")
        if "1<2" in res:
            return self._wait_until_idle()
        else:
            return False

    def close(self) -> bool:
        """Closes port. Returns True."""
        if self.ser:
            self.ser.close()
        return True

# --- EXAMPLE OF ATOMIC LOGIC ---
if __name__ == "__main__":
    pipette = WrapperSP28Pipettor(port='/dev/ttyUSB0')
    
    if pipette.connect():
        # Using the results as simple True/False switches
        if pipette.has_tip():
            print("Action: Tip Found. Proceeding.")
            if pipette.aspirate(50):
                print("Action: Aspiration Complete.")
        else:
            print("Action: No Tip. Aborting.")
        
        pipette.close()

import serial.tools.list_ports

def auto_connect(self) -> bool:
    """
    Loops over all available ports and handshakes with the pipettor.
    Returns True if found, False if not.
    """
    # Get all available serial ports on the system
    ports = list(serial.tools.list_ports.comports())
    
    for p in ports:
        try:
            # Try to open the port
            test_ser = serial.Serial(p.device, self.baud, timeout=0.5)
            
            # Send the Query command: 1>? followed by Carriage Return
            test_ser.write(b"1>?\r")
            time.sleep(0.1)
            
            # Check the response
            response = test_ser.readline().decode('ascii').strip()
            test_ser.close()

            # If it responds with '1<', this is the SP28
            if "1<" in response:
                self.port = p.device
                self.ser = serial.Serial(self.port, self.baud, timeout=2)
                return True
        except:
            # Port busy or no response, move to next
            continue
            
    return False