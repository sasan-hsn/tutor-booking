import re

from django.core.exceptions import ValidationError

_ETH_ADDRESS_RE = re.compile(r"^0x[0-9a-fA-F]{40}$")

# Keccak-f[1600] round constants and rotation offsets (original Keccak, NOT NIST SHA-3).
_KECCAK_RC = (
    0x0000000000000001,
    0x0000000000008082,
    0x800000000000808A,
    0x8000000080008000,
    0x000000000000808B,
    0x0000000080000001,
    0x8000000080008081,
    0x8000000000008009,
    0x000000000000008A,
    0x0000000000000088,
    0x0000000080008009,
    0x000000008000000A,
    0x000000008000808B,
    0x800000000000008B,
    0x8000000000008089,
    0x8000000000008003,
    0x8000000000008002,
    0x8000000000000080,
    0x000000000000800A,
    0x800000008000000A,
    0x8000000080008081,
    0x8000000000008080,
    0x0000000080000001,
    0x8000000080008008,
)

_KECCAK_R = (
    (0, 36, 3, 41, 18),
    (1, 44, 10, 45, 2),
    (62, 6, 43, 15, 61),
    (28, 55, 25, 21, 56),
    (27, 20, 39, 8, 14),
)

_MASK64 = 0xFFFFFFFFFFFFFFFF


def _rotl64(value: int, shift: int) -> int:
    shift %= 64
    if shift == 0:
        return value
    return ((value << shift) | (value >> (64 - shift))) & _MASK64


def _keccak_f1600(state: list[list[int]]) -> None:
    for rc in _KECCAK_RC:
        # Theta
        c = [
            state[x][0] ^ state[x][1] ^ state[x][2] ^ state[x][3] ^ state[x][4]
            for x in range(5)
        ]
        d = [c[(x - 1) % 5] ^ _rotl64(c[(x + 1) % 5], 1) for x in range(5)]
        for x in range(5):
            for y in range(5):
                state[x][y] ^= d[x]

        # Rho & Pi
        b = [[0] * 5 for _ in range(5)]
        for x in range(5):
            for y in range(5):
                b[y][(2 * x + 3 * y) % 5] = _rotl64(state[x][y], _KECCAK_R[x][y])

        # Chi
        for x in range(5):
            for y in range(5):
                state[x][y] = b[x][y] ^ (
                    ((~b[(x + 1) % 5][y]) & _MASK64) & b[(x + 2) % 5][y]
                )

        # Iota
        state[0][0] ^= rc


def keccak_256(data: bytes) -> bytes:
    """Compute original Keccak-256 digest (used by Ethereum EIP-55)."""
    rate_bytes = 136  # 1088 bits
    padded = bytearray(data)
    padded.append(0x01)
    while len(padded) % rate_bytes != 0:
        padded.append(0x00)
    padded[-1] |= 0x80

    state = [[0] * 5 for _ in range(5)]
    for offset in range(0, len(padded), rate_bytes):
        block = padded[offset : offset + rate_bytes]
        for i in range(rate_bytes // 8):
            lane = int.from_bytes(block[i * 8 : (i + 1) * 8], "little")
            state[i % 5][i // 5] ^= lane
        _keccak_f1600(state)

    out = bytearray()
    for i in range(4):
        out.extend(state[i % 5][i // 5].to_bytes(8, "little"))
    return bytes(out)


def to_eip55_address(hex_body: str) -> str:
    """Convert a 40-character hex address body (without 0x) into canonical EIP-55 format."""
    lower_hex = hex_body.lower()
    digest_hex = keccak_256(lower_hex.encode("ascii")).hex()
    checksummed = "".join(
        ch.upper() if int(digest_hex[i], 16) >= 8 else ch
        for i, ch in enumerate(lower_hex)
    )
    return f"0x{checksummed}"


def validate_bep20_address(raw_address: str) -> str:
    """
    Validate a BEP-20 (BNB Smart Chain / EVM) wallet address using strict EIP-55 rules.
    Accepts canonical mixed-case EIP-55 addresses as well as all-lowercase or all-uppercase
    40-hex-character addresses (normalizing them to EIP-55 checksum format).
    Rejects invalid formats and mixed-case addresses with checksum mismatches.
    """
    cleaned = (raw_address or "").strip()
    if not _ETH_ADDRESS_RE.match(cleaned):
        raise ValidationError(
            "Enter a valid BEP-20 wallet address starting with 0x followed by 40 hexadecimal characters."
        )

    hex_body = cleaned[2:]
    canonical = to_eip55_address(hex_body)
    is_all_lower = hex_body == hex_body.lower()
    is_all_upper = hex_body == hex_body.upper()

    if not is_all_lower and not is_all_upper and cleaned != canonical:
        raise ValidationError(
            "Invalid BEP-20 address checksum (EIP-55 verification failed). "
            "Please double-check the capitalization of your wallet address."
        )

    return canonical
