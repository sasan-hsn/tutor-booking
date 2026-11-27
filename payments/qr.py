import base64
from typing import Final

# ISO/IEC 18004 Version 1..6 parameters for Error Correction Level M:
# (size, data_codewords, ec_codewords_per_block, num_blocks, alignment_centers)
_QR_PARAMS_M: Final[dict[int, tuple[int, int, int, int, tuple[int, ...]]]] = {
    1: (21, 16, 10, 1, ()),
    2: (25, 28, 16, 1, (6, 18)),
    3: (29, 44, 26, 1, (6, 22)),
    4: (33, 64, 18, 2, (6, 26)),
    5: (37, 86, 24, 2, (6, 30)),
    6: (41, 108, 16, 4, (6, 34)),
}

_GF_EXP = [0] * 512
_GF_LOG = [0] * 256
_val = 1
for _i in range(255):
    _GF_EXP[_i] = _val
    _GF_LOG[_val] = _i
    _val <<= 1
    if _val & 0x100:
        _val ^= 0x11D
for _i in range(255, 512):
    _GF_EXP[_i] = _GF_EXP[_i - 255]


def _gf_mul(a: int, b: int) -> int:
    if a == 0 or b == 0:
        return 0
    return _GF_EXP[_GF_LOG[a] + _GF_LOG[b]]


def _rs_generator_poly(degree: int) -> list[int]:
    poly = [1]
    for i in range(degree):
        root = _GF_EXP[i]
        nxt = [0] * (len(poly) + 1)
        for j, coeff in enumerate(poly):
            nxt[j] ^= coeff
            nxt[j + 1] ^= _gf_mul(coeff, root)
        poly = nxt
    return poly


def _rs_compute_ec(data: list[int], ec_count: int) -> list[int]:
    gen = _rs_generator_poly(ec_count)
    buf = list(data) + [0] * ec_count
    for i in range(len(data)):
        factor = buf[i]
        if factor != 0:
            for j, g in enumerate(gen):
                buf[i + j] ^= _gf_mul(g, factor)
    return buf[len(data) :]


def _select_version(byte_len: int) -> int:
    # Byte mode overhead: 4-bit mode + 8-bit char count (versions 1..9) + 4-bit terminator = 2 bytes.
    required_bytes = byte_len + 2
    for ver, (_, data_cap, _, _, _) in _QR_PARAMS_M.items():
        if required_bytes <= data_cap:
            return ver
    raise ValueError("QR payload exceeds supported length.")


def _encode_codewords(payload: bytes, version: int) -> list[int]:
    _, data_cap, ec_per_block, num_blocks, _ = _QR_PARAMS_M[version]
    bits: list[int] = []

    def push_bits(value: int, width: int) -> None:
        for shift in range(width - 1, -1, -1):
            bits.append((value >> shift) & 1)

    push_bits(0b0100, 4)  # Byte mode
    push_bits(len(payload), 8)
    for b in payload:
        push_bits(b, 8)

    max_bits = data_cap * 8
    terminator_len = min(4, max_bits - len(bits))
    push_bits(0, terminator_len)
    while len(bits) % 8 != 0:
        bits.append(0)

    data_cw = [
        sum(bits[i + j] << (7 - j) for j in range(8)) for i in range(0, len(bits), 8)
    ]
    pad_bytes = (0xEC, 0x11)
    pad_idx = 0
    while len(data_cw) < data_cap:
        data_cw.append(pad_bytes[pad_idx % 2])
        pad_idx += 1

    # Split into equal blocks (versions 1..6 ECC M all divide evenly into equal blocks)
    block_size = data_cap // num_blocks
    data_blocks: list[list[int]] = []
    ec_blocks: list[list[int]] = []
    for b in range(num_blocks):
        block = data_cw[b * block_size : (b + 1) * block_size]
        data_blocks.append(block)
        ec_blocks.append(_rs_compute_ec(block, ec_per_block))

    interleaved: list[int] = []
    for col in range(block_size):
        for block in data_blocks:
            interleaved.append(block[col])
    for col in range(ec_per_block):
        for block in ec_blocks:
            interleaved.append(block[col])
    return interleaved


def _place_finder(grid: list[list[int | None]], row: int, col: int) -> None:
    size = len(grid)
    for r in range(-1, 8):
        for c in range(-1, 8):
            rr, cc = row + r, col + c
            if 0 <= rr < size and 0 <= cc < size:
                if 0 <= r <= 6 and 0 <= c <= 6:
                    is_dark = (
                        r in (0, 6) or c in (0, 6) or (2 <= r <= 4 and 2 <= c <= 4)
                    )
                    grid[rr][cc] = 1 if is_dark else 0
                else:
                    grid[rr][cc] = 0


def _place_alignment(grid: list[list[int | None]], row: int, col: int) -> None:
    for r in range(-2, 3):
        for c in range(-2, 3):
            is_dark = max(abs(r), abs(c)) in (0, 2)
            grid[row + r][col + c] = 1 if is_dark else 0


def _format_bits_m(mask_id: int) -> int:
    # ECC M format indicator is 00; 5-bit format data = (00 << 3) | mask_id
    data = mask_id & 0b111
    rem = data << 10
    gen = 0b10100110111
    for shift in range(14, 9, -1):
        if rem & (1 << shift):
            rem ^= gen << (shift - 10)
    return ((data << 10) | rem) ^ 0b101010000010010


def _build_base_matrix(version: int) -> tuple[list[list[int | None]], list[list[bool]]]:
    size, _, _, _, align = _QR_PARAMS_M[version]
    grid: list[list[int | None]] = [[None] * size for _ in range(size)]

    _place_finder(grid, 0, 0)
    _place_finder(grid, 0, size - 7)
    _place_finder(grid, size - 7, 0)

    # Timing patterns
    for i in range(8, size - 8):
        val = 1 if i % 2 == 0 else 0
        if grid[6][i] is None:
            grid[6][i] = val
        if grid[i][6] is None:
            grid[i][6] = val

    # Alignment patterns
    for r in align:
        for c in align:
            if grid[r][c] is None:
                _place_alignment(grid, r, c)

    # Dark module
    grid[4 * version + 9][8] = 1

    # Reserve format information areas
    for i in range(9):
        if grid[8][i] is None:
            grid[8][i] = 0
        if grid[i][8] is None:
            grid[i][8] = 0
    for i in range(8):
        if grid[8][size - 1 - i] is None:
            grid[8][size - 1 - i] = 0
    for i in range(7):
        if grid[size - 1 - i][8] is None:
            grid[size - 1 - i][8] = 0

    is_function = [[cell is not None for cell in row] for row in grid]
    return grid, is_function


def _apply_format_info(grid: list[list[int]], mask_id: int) -> None:
    size = len(grid)
    fmt = _format_bits_m(mask_id)
    for i in range(6):
        bit = (fmt >> i) & 1
        grid[8][i] = bit
        grid[size - 1 - i][8] = bit
    grid[8][7] = (fmt >> 6) & 1
    grid[size - 7][8] = (fmt >> 6) & 1
    grid[8][8] = (fmt >> 7) & 1
    grid[8][size - 8] = (fmt >> 7) & 1
    grid[7][8] = (fmt >> 8) & 1
    grid[8][size - 7] = (fmt >> 8) & 1
    for i in range(9, 15):
        bit = (fmt >> i) & 1
        grid[14 - i][8] = bit
        grid[8][size - 15 + i] = bit


def _mask_condition(mask_id: int, r: int, c: int) -> bool:
    if mask_id == 0:
        return (r + c) % 2 == 0
    if mask_id == 1:
        return r % 2 == 0
    if mask_id == 2:
        return c % 3 == 0
    if mask_id == 3:
        return (r + c) % 3 == 0
    if mask_id == 4:
        return (r // 2 + c // 3) % 2 == 0
    if mask_id == 5:
        return (r * c) % 2 + (r * c) % 3 == 0
    if mask_id == 6:
        return ((r * c) % 2 + (r * c) % 3) % 2 == 0
    return ((r + c) % 2 + (r * c) % 3) % 2 == 0


def _line_penalty(line: list[int]) -> int:
    penalty = 0
    run = 1
    for idx in range(1, len(line)):
        if line[idx] == line[idx - 1]:
            run += 1
        else:
            if run >= 5:
                penalty += 3 + (run - 5)
            run = 1
    if run >= 5:
        penalty += 3 + (run - 5)

    pat1 = [1, 0, 1, 1, 1, 0, 1, 0, 0, 0, 0]
    pat2 = [0, 0, 0, 0, 1, 0, 1, 1, 1, 0, 1]
    for idx in range(len(line) - 10):
        seg = line[idx : idx + 11]
        if seg == pat1 or seg == pat2:
            penalty += 40
    return penalty


def _penalty_score(grid: list[list[int]]) -> int:
    size = len(grid)
    score = 0

    for r in range(size):
        score += _line_penalty(grid[r])
    for c in range(size):
        score += _line_penalty([grid[r][c] for r in range(size)])

    for r in range(size - 1):
        for c in range(size - 1):
            v = grid[r][c]
            if v == grid[r][c + 1] == grid[r + 1][c] == grid[r + 1][c + 1]:
                score += 3

    dark = sum(sum(row) for row in grid)
    pct = (dark * 100) // (size * size)
    prev_mult = (pct // 5) * 5
    next_mult = prev_mult + 5
    score += min(abs(prev_mult - 50) // 5, abs(next_mult - 50) // 5) * 10
    return score


def build_qr_matrix(data: str) -> list[list[int]]:
    """Build a standard ISO/IEC 18004 QR Code matrix (ECC Level M) for `data`."""
    payload = data.encode("utf-8")
    version = _select_version(len(payload))
    codewords = _encode_codewords(payload, version)
    base_grid, is_function = _build_base_matrix(version)
    size = len(base_grid)

    bit_stream: list[int] = []
    for cw in codewords:
        for shift in range(7, -1, -1):
            bit_stream.append((cw >> shift) & 1)

    bit_idx = 0
    col = size - 1
    upward = True
    while col > 0:
        if col == 6:
            col -= 1
        row_range = range(size - 1, -1, -1) if upward else range(size)
        for r in row_range:
            for dc in (0, -1):
                cc = col + dc
                if not is_function[r][cc]:
                    bit = bit_stream[bit_idx] if bit_idx < len(bit_stream) else 0
                    bit_idx += 1
                    base_grid[r][cc] = bit
        col -= 2
        upward = not upward

    best_grid: list[list[int]] | None = None
    best_score = float("inf")
    for mask_id in range(8):
        candidate: list[list[int]] = [
            [
                (
                    int(base_grid[r][c] or 0)
                    ^ (
                        1
                        if not is_function[r][c] and _mask_condition(mask_id, r, c)
                        else 0
                    )
                )
                for c in range(size)
            ]
            for r in range(size)
        ]
        _apply_format_info(candidate, mask_id)
        score = _penalty_score(candidate)
        if score < best_score:
            best_score = score
            best_grid = candidate

    assert best_grid is not None
    return best_grid


def render_qr_svg(data: str, *, quiet_zone: int = 4) -> str:
    """Render `data` as a crisp, self-contained SVG QR code string."""
    matrix = build_qr_matrix(data)
    dim = len(matrix) + quiet_zone * 2
    path_parts = [
        f"M{c + quiet_zone},{r + quiet_zone}h1v1h-1z"
        for r, row in enumerate(matrix)
        for c, cell in enumerate(row)
        if cell
    ]
    path_d = "".join(path_parts)
    return (
        f'<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 {dim} {dim}" '
        f'shape-rendering="crispEdges" role="img" aria-label="Deposit address QR code">'
        f'<rect width="{dim}" height="{dim}" fill="#ffffff"/>'
        f'<path d="{path_d}" fill="#111827"/>'
        f"</svg>"
    )


def render_qr_data_uri(data: str) -> str:
    """Return a base64-encoded SVG data URI for `data`."""
    svg_bytes = render_qr_svg(data).encode("utf-8")
    encoded = base64.b64encode(svg_bytes).decode("ascii")
    return f"data:image/svg+xml;base64,{encoded}"
