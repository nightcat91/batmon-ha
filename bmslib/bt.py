from bleak import BleakClient

from .bms import BmsSample


class BtBms():
    def __init__(self, address, name):
        self.client = BleakClient(address)
        self.name = name

    async def connect(self):
        await self.client.connect()

    async def disconnect(self):
        await self.client.disconnect()

    async def fetch(self) -> BmsSample:
        raise NotImplementedError()

    async def fetch_voltages(self):
        raise NotImplementedError()

    async def fetch_temperatures(self):
        raise NotImplementedError()

    def __str__(self):
        return f'{self.__class__.__name__}({self.client.address})'

    async def __aenter__(self):
        # print("enter")
        await self.connect()

    async def __aexit__(self, *args):
        # print("exit")
        await self.disconnect()

    def __await__(self):
        return self.__aexit__().__await__()

