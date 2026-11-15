"""Read Steam's account-scoped license cache; never expose license credentials.

Format references are documented in docs/research.md. The cache XOR stream is
Valve's shuffled Park-Miller generator seeded with the signed account ID. A
CRC32 protects the decoded protobuf; packageinfo maps package IDs to app IDs.
All decoding stays in memory, and only entitlement fields leave the decoder.
"""
from dataclasses import dataclass
import struct
import zlib

from .formats import BinaryKV, FormatError, at


@dataclass(frozen=True)
class License:
    package_id: int
    flags: int = 0
    license_type: int = 0
    minute_limit: int = 0
    minutes_used: int = 0


def cache_bytes(data, account):
    """Symmetric cache obfuscation, independent of account passwords/tickets."""
    account = int(account)
    if not 0 < account <= 0xffffffff:
        raise FormatError('许可证缓存的账号编号无效')
    seed = struct.unpack('<i', struct.pack('<I', account))[0]
    state = max(1, abs(seed))
    shuffle = [0] * 32
    modulus = 2147483647
    for index in range(39, -1, -1):
        state = (state * 16807) % modulus
        if index < 32:
            shuffle[index] = state
    previous = shuffle[0]
    result = bytearray()
    limit = modulus - ((modulus + 1) % 95)
    for value in data:
        while True:
            state = (state * 16807) % modulus
            index = previous // 67108864
            previous, shuffle[index] = shuffle[index], state
            if previous <= limit:
                result.append(value ^ (32 + previous % 95))
                break
    return bytes(result)


def protobuf_fields(data):
    """Bounded wire reader. Unknown fields are consumed without interpretation."""
    position = 0

    def take(size):
        nonlocal position
        end = position + size
        if end > len(data):
            raise FormatError('许可证数据不完整')
        value = data[position:end]
        position = end
        return value

    def varint():
        value = 0
        for shift in range(0, 70, 7):
            byte = take(1)[0]
            if shift == 63 and byte > 1:
                raise FormatError('许可证数值溢出')
            value |= (byte & 127) << shift
            if not byte & 128:
                return value
        raise FormatError('许可证数值无效')

    while position < len(data):
        tag = varint()
        field, wire = tag >> 3, tag & 7
        if field == 0 or field > 0x1fffffff:
            raise FormatError('许可证字段编号无效')
        if wire == 0:
            value = varint()
        elif wire in (1, 5):
            value = int.from_bytes(take(8 if wire == 1 else 4), 'little')
        elif wire == 2:
            value = take(varint())
        else:
            raise FormatError('不支持的许可证字段格式')
        yield field, wire, value


def decode_licenses(data, account):
    if len(data) < 6 or len(data) > 64 * 1024 * 1024:
        raise FormatError('许可证缓存长度无效')
    decoded = cache_bytes(data, account)
    message, checksum = decoded[:-4], decoded[-4:]
    if zlib.crc32(message) != int.from_bytes(checksum, 'little'):
        raise FormatError('许可证缓存校验失败，可能正在同步或格式已变化')
    result, licenses = None, []
    for field, wire, value in protobuf_fields(message):
        if field == 1:
            if wire != 0:
                raise FormatError('许可证缓存状态字段无效')
            result = value
        elif field == 2:
            if wire != 2:
                raise FormatError('许可证缓存记录无效')
            fields = {}
            for number, field_wire, item in protobuf_fields(value):
                if number in (1, 4, 5, 7, 9):
                    if field_wire != 0:
                        raise FormatError('许可证授权字段无效')
                    fields[number] = item
                # In particular, never retain access_token / owner_id / country.
            if 1 not in fields or fields[1] > 0xffffffff:
                raise FormatError('许可证缺少有效的包编号')
            signed = lambda x: (x & 0x7fffffff) - (x & 0x80000000)
            licenses.append(License(fields[1], fields.get(7, 0), fields.get(9, 0),
                                    signed(fields.get(4, 0)), signed(fields.get(5, 0))))
    if result != 1:
        raise FormatError('Steam 许可证列表未成功同步')
    return licenses


def packageinfo(data, wanted):
    reader = BinaryKV(data)
    magic = reader.number('<I')
    universe = reader.number('<I')
    if magic not in (0x06565527, 0x06565528) or universe != 1:
        raise FormatError('尚未支持的 Steam 包信息版本')
    result = {}
    while True:
        package_id = reader.number('<I')
        if package_id == 0xffffffff:
            return result
        reader.take(24)  # SHA1 and change number; neither is an entitlement.
        if magic == 0x06565528:
            reader.take(8)  # PICS access token is intentionally discarded.
        record = reader.object()
        if package_id in wanted:
            value = record.get(str(package_id))
            if not isinstance(value, dict):
                raise FormatError('Steam 包信息结构无效')
            result[package_id] = value


# Steam ELicenseFlags: pending, expired, cancellations, unactivated licenses.
# RegionRestrictionExpired (0x200) is NOT the Expired (0x08) entitlement flag.
INACTIVE_FLAGS = 0x04 | 0x08 | 0x10 | 0x20 | 0x400 | 0x800 | 0x40000


def licensed_apps(licenses, packages, now, *, include_default_package=True):
    apps, missing = set(), set()
    next_expiry = None
    for license in licenses:
        if not include_default_package and license.package_id == 0:
            continue
        if license.flags & INACTIVE_FLAGS or not license.license_type:
            continue
        if license.minute_limit > 0 and license.minutes_used >= license.minute_limit:
            continue
        package = packages.get(license.package_id)
        if package is None:
            missing.add(license.package_id)
            continue
        try:
            expiry = int(at(package, 'extended').get('expirytime', 0))
        except (TypeError, ValueError):
            raise FormatError('Steam 包授权截止时间无效') from None
        if expiry and expiry <= now:
            continue
        if expiry:
            next_expiry = min(next_expiry, expiry) if next_expiry else expiry
        appids = package.get('appids', {})
        if not isinstance(appids, dict):
            raise FormatError('Steam 包的游戏列表无效')
        for value in appids.values():
            if not isinstance(value, (int, str)) or isinstance(value, bool):
                raise FormatError('Steam 包的游戏编号无效')
            try:
                appid = int(value)
            except ValueError:
                raise FormatError('Steam 包的游戏编号无效') from None
            if not 0 < appid <= 0xffffffff:
                raise FormatError('Steam 包的游戏编号超出范围')
            apps.add(appid)
    return apps, missing, next_expiry
