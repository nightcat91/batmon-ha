"""
Supervolt protocol

Code mostly taken from
   https://github.com/BikeAtor/WoMoAtor

References
    - 

"""
import sys, time
import asyncio

from bmslib.bms import BmsSample
from bmslib.bt import BtBms


class SuperVoltBt(BtBms):
    UUID_RX = '0000ff01-0000-1000-8000-00805f9b34fb'  # Read Characteristic UUID
    UUID_TX = '0000ff02-0000-1000-8000-00805f9b34fb'  # Write Characteristic UUID
    TIMEOUT = 8

    def __init__(self, address, **kwargs):
        super().__init__(address, **kwargs)
        self.notificationReceived = False

        self.data = None
        self._switches = None
    
        self.num_cell = 4
        self.num_temp = 1

        self.cellV = [None] * 16
        self.totalV = None
        self.soc = None
        self.workingState = None
        self.alarm = None
        self.chargingA = None
        self.dischargingA = None
        self.loadA = None
        self.tempC = [None] * 4
        self.completeAh = None
        self.remainingAh = None
        self.designedAh = None
        self.dischargeNumber = None
        self.chargeNumber = None

    def _notification_handler(self, sender, data):
        """
        Notification handler for the battery
        It has special handling for the data received from the battery
        Some SuperVolt batteries send the data in multiple chunks, so we need to combine them
        """
        if self.verbose_log:
            self.logger.info(f"notification: {data.hex()} {sender}")
        if data is not None:
            # ':' is the start of a new data set
            if data[0] == ord(':'):
                self.data = data
            else:
                self.data += data
            # Check if self.data is complete, it should start with ':' and end with '~'
            if self.data[0] == ord(':') and data[-1] == ord('~'):
                self.parseData(self.data)
                self.lastUpdatetime = time.time()
                self.notificationReceived = True
        else:
            self.data = None
            self.notificationReceived = True

    async def waitForNotification(self, timeS: float) -> bool:
        start = time.time()
        await asyncio.sleep(0.1)
        while time.time() - start < timeS and not self.notificationReceived:
            await asyncio.sleep(0.1)
        return self.notificationReceived

    async def connect(self, **kwargs):
        await super().connect(**kwargs)
        await self.client.start_notify(self.UUID_RX, self._notification_handler)

    async def disconnect(self):
        await self.client.stop_notify(self.UUID_RX)
        self._fetch_futures.clear()
        await super().disconnect()

    # send request to battery for Realtime-Data
    async def requestRealtimeData(self):
        data = bytes(":000250000E03~", "ascii")
        handle = self.UUID_TX  # Use class attribute
        try:
            ret = await self.client.write_gatt_char(char_specifier=handle, data=data)
            if self.verbose_log:
                self.logger.debug(f"requestRealtimeData: {ret} {data}")
        except Exception as e:
            self.logger.error(f"Error in requestRealtimeData: {e}", exc_info=True)

    # send request to battery for Capacity-Data
    async def requestCapacity(self):
        data = bytes(":001031000E05~", "ascii")
        handle = self.UUID_TX  # Use class attribute
        try:
            ret = await self.client.write_gatt_char(char_specifier=handle, data=data)
            if self.verbose_log:
                self.logger.debug(f"requestCapacity: {ret} {data}")
        except Exception as e:
            self.logger.error(f"Error in requestCapacity: {e}", exc_info=True)

    async def requestData(self):
        try:
            await self.requestRealtimeData()
            await self.waitForNotification(10.0)
            
            await self.requestCapacity()
            await self.waitForNotification(10.0)
        except Exception as e:
            self.logger.error(f"Error in requestData: {e}", exc_info=True)

    # try to read values from data
    def parseData(self, data):
        if self.verbose_log:
            self.logger.debug(f"parseData: {len(data)}")
        try:
            if data:
                if len(data) == 128:
                    if self.verbose_log:
                        self.logger.info(f"parse Realtimedata: {type(data)}")
                    if type(data) is bytearray:
                        data = bytes(data)
                    if type(data) is bytes:
                        # Parsing logic based on the document
                        start = 1
                        end = start + 2
                        self.address = int(data[start: end].decode(), 16)
                        if self.verbose_log:
                            self.logger.debug(f"address: {self.address}")

                        start = end
                        end = start + 2
                        self.command = int(data[start: end].decode(), 16)
                        if self.verbose_log:
                            self.logger.debug(f"command: {self.command}")

                        start = end
                        end = start + 2
                        self.version = int(data[start: end].decode(), 16)
                        if self.verbose_log:
                            self.logger.debug(f"version: {self.version}")

                        start = end
                        end = start + 4
                        self.length = int(data[start: end].decode(), 16)
                        if self.verbose_log:
                            self.logger.debug(f"length: {self.length}")

                        start = end
                        end = start + 14
                        bdate = data[start: end]
                        if self.verbose_log:
                            self.logger.debug(f"date: {bdate}")

                        start = end
                        end = start + 16 * 4
                        bvoltarray = data[start: end]
                        self.totalV = 0
                        for i in range(11):
                            bvolt = data[(start + i * 4):(start + i * 4 + 4)]
                            self.cellV[i] = int(bvolt.decode(), 16)
                            self.totalV += self.cellV[i] * 1e-3
                            if self.verbose_log:
                                self.logger.debug(f"volt{i}: {bvolt} / {self.cellV[i]}V")

                        if self.verbose_log:
                            self.logger.debug(f"totalVolt: {self.totalV}")

                        start = end
                        end = start + 4
                        bcharging = data[start: end]
                        self.chargingA = int(bcharging.decode(), 16) / 100.0
                        if self.verbose_log:
                            self.logger.debug(f"charging: {bcharging} / {self.chargingA}A")
                        if self.chargingA > 500:
                            # problem with supervolt
                            self.logger.info(f"charging too big: {self.chargingA}")
                            self.chargingA = 0.0

                        start = end
                        end = start + 4
                        bdischarging = data[start: end]
                        self.dischargingA = int(bdischarging.decode(), 16) / 100.0
                        if self.verbose_log:
                            self.logger.debug(f"discharging: {bdischarging} / {self.dischargingA}A")
                        if self.dischargingA > 500:
                            # problem with supervolt
                            self.logger.info(f"discharging too big: {self.dischargingA}")
                            self.dischargingA = 0.0

                        self.loadA = -self.chargingA + self.dischargingA
                        if self.verbose_log:
                            self.logger.debug(f"loadA: {self.loadA}A")

                        for i in range(4):
                            start = end
                            end = start + 2
                            btemp = data[start: end]
                            self.tempC[i] = int(btemp.decode(), 16) - 40
                            if self.verbose_log:
                                self.logger.debug(f"temp{i}: {btemp} / {self.tempC[i]}°C")

                        start = end
                        end = start + 4
                        self.workingState = int(data[start: end].decode(), 16)
                        if self.verbose_log:
                            self.logger.debug(f"workingstate: {self.workingState} / {data[start: end]} / {self.getWorkingStateTextShort()} / {self.getWorkingStateText()}")

                        start = end
                        end = start + 2
                        self.alarm = int(data[start: end].decode(), 16)
                        if self.verbose_log:
                            self.logger.debug(f"alarm: {self.alarm}")

                        start = end
                        end = start + 4
                        self.balanceState = int(data[start: end].decode(), 16)
                        if self.verbose_log:
                            self.logger.debug(f"balanceState: {self.balanceState}")

                        start = end
                        end = start + 4
                        self.dischargeNumber = int(data[start: end].decode(), 16)
                        if self.verbose_log:
                            self.logger.debug(f"dischargeNumber: {self.dischargeNumber}")

                        start = end
                        end = start + 4
                        self.chargeNumber = int(data[start:end].decode(), 16)
                        end = start + 4
                        self.chargeNumber = int(data[start: end].decode(), 16)
                        if self.verbose_log:
                            self.logger.debug(f"chargeNumber: {self.chargeNumber}")

                        # State of Charge (%)
                        start = end
                        end = start + 2
                        self.soc = int(data[start: end].decode(), 16)
                        if self.verbose_log:
                            self.logger.debug(f"soc: {self.soc}")
                            self.logger.info("end of parse realtimedata")
                            self.logger.debug(f"end code: {int(data[end:128-1].decode(), 16)}")

                    else:
                        self.logger.warning("no bytes")
                elif len(data) == 30:
                    if self.verbose_log:
                        self.logger.debug("capacity")
                    if type(data) is bytearray:
                        data = bytes(data)
                    if type(data) is bytes:
                        start = 1
                        end = start + 2
                        self.address = int(data[start: end].decode(), 16)
                        if self.verbose_log:
                            self.logger.debug(f"address: {self.address}")

                        start = end
                        end = start + 2
                        self.command = int(data[start: end].decode(), 16)
                        if self.verbose_log:
                            self.logger.debug(f"command: {self.command}")

                        start = end
                        end = start + 2
                        self.version = int(data[start: end].decode(), 16)
                        if self.verbose_log:
                            self.logger.debug(f"version: {self.version}")

                        start = end
                        end = start + 4
                        self.length = int(data[start: end].decode(), 16)
                        if self.verbose_log:
                            self.logger.debug(f"length: {self.length}")

                        start = end
                        end = start + 4
                        breseved = data[start: end]
                        if self.verbose_log:
                            self.logger.debug(f"reseved: {breseved}")

                        start = end
                        end = start + 4
                        self.remainingAh = int(data[start: end].decode(), 16) / 10.0
                        if self.verbose_log:
                            self.logger.debug(f"remainingAh: {self.remainingAh} / {data[start: end]}")

                        start = end
                        end = start + 4
                        self.completeAh = int(data[start: end].decode(), 16) / 10.0
                        if self.verbose_log:
                            self.logger.debug(f"completeAh: {self.completeAh}")

                        start = end
                        end = start + 4
                        self.designedAh = int(data[start: end].decode(), 16) / 10.0
                        if self.verbose_log:
                            self.logger.debug(f"designedAh: {self.designedAh}")
                            self.logger.info("end of parse capacity")
                            self.logger.debug(f"end code: {int(data[end:30-1].decode(), 16)}")
                else:
                    self.logger.warning(f"wrong length: {len(data)}")
            else:
                self.logger.debug("no data")
        except Exception as e:
            self.logger.error(f"Error in parseData: {e}", exc_info=True)

    def getWorkingStateTextShort(self):
        if self.workingState is None:
            return "nicht erreichbar"
        if self.workingState & 0xF003 >= 0xF000:
            return "Normal"
        if self.workingState & 0x000C > 0x0000:
            return "Schutzschaltung"
        if self.workingState & 0x0020 > 0:
            return "Kurzschluss"
        if self.workingState & 0x0500 > 0:
            return "Überhitzt"
        if self.workingState & 0x0A00 > 0:
            return "Unterkühlt"
        return "Unbekannt"

    def getWorkingStateText(self):
        text = ""
        if self.workingState is None:
            return "Unbekannt"
        if self.workingState & 0x0001 > 0:
            text = self.appendState(text, "Laden")
        if self.workingState & 0x0002 > 0:
            text = self.appendState(text, "Entladen")
        if self.workingState & 0x0004 > 0:
            text = self.appendState(text, "Überladungsschutz")
        if self.workingState & 0x0008 > 0:
            text = self.appendState(text, "Entladeschutz")
        if self.workingState & 0x0010 > 0:
            text = self.appendState(text, "Überladen")
        if self.workingState & 0x0020 > 0:
            text = self.appendState(text, "Kurzschluss")
        if self.workingState & 0x0040 > 0:
            text = self.appendState(text, "Entladeschutz 1")
        if self.workingState & 0x0080 > 0:
            text = self.appendState(text, "Entladeschutz 2")
        if self.workingState & 0x0100 > 0:
            text = self.appendState(text, "Überhitzt (Laden)")
        if self.workingState & 0x0200 > 0:
            text = self.appendState(text, "Unterkühlt (Laden)")
        if self.workingState & 0x0400 > 0:
            text = self.appendState(text, "Überhitzt (Entladen)")
        if self.workingState & 0x0800 > 0:
            text = self.appendState(text, "Unterkühlt (Entladen)")
        if self.workingState & 0x1000 > 0:
            text = self.appendState(text, "DFET an")
        if self.workingState & 0x2000 > 0:
            text = self.appendState(text, "CFET an")
        if self.workingState & 0x4000 > 0:
            text = self.appendState(text, "DFET Schalter an")
        if self.workingState & 0x8000 > 0:
            text = self.appendState(text, "CFET Schalter an")

        return text

    def appendState(self, text, append):
        if text is None or len(text) == 0:
            return append
        return text + " | " + append

    async def fetch(self) -> BmsSample:
        await self.requestData()

        sample = BmsSample(
            voltage=self.totalV,
            current=self.loadA,

            soc=self.soc,

            charge=self.remainingAh,
            capacity=self.completeAh,

            num_cycles=self.dischargeNumber,

            temperatures=self.tempC[:self.num_temp],
            mos_temperature=self.tempC[0],

            switches=dict(
                status_discharging=(self.workingState is not None and (self.workingState & 0x0002 > 0)),
                status_charging=(self.workingState is not None and (self.workingState & 0x0001 > 0)),
                status_normal=(self.workingState is not None and (self.workingState & 0xF003 >= 0xF000)),
                status_protection=(self.workingState is not None and (self.workingState & 0x000C > 0)),
                status_short=(self.workingState is not None and (self.workingState & 0x0020 > 0)),
                status_overtemp=(self.workingState is not None and (self.workingState & 0x0500 > 0)),
                status_undertemp=(self.workingState is not None and (self.workingState & 0x0A00 > 0)),
                status_overvolt_protection=(self.workingState is not None and (self.workingState & 0x0004 > 0)),
                status_undervolt_protection=(self.workingState is not None and (self.workingState & 0x0008 > 0))
            )
        )

        self._switches = dict(sample.switches)

        return sample

    async def fetch_voltages(self):
        return self.cellV[:self.num_cell]

async def main():
    mac_address = "84:28:D7:8F:XX:XX"  # Replace with your device's MAC address
    bms = SuperVoltBt(mac_address, name='supervolt')
    await bms.connect()
    sample = await bms.fetch()
    print(sample)
    voltages = await bms.fetch_voltages()
    print(voltages)
    await bms.disconnect()

if __name__ == '__main__':
    asyncio.run(main())
