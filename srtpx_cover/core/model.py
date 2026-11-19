"""Mô hình dữ liệu của file .srtpx (thuần Python, không Qt)."""
from __future__ import annotations

from dataclasses import dataclass, field


@dataclass(frozen=True)
class Box:
    """Hộp chữ nhật theo toạ độ #res: x1/x2 là mép trái/phải, y1/y2 là mép trên/dưới.

    Gốc ở góc trên trái. Các mép là toạ độ liên tục (không phải chỉ số điểm ảnh).
    """

    x1: float
    x2: float
    y1: float
    y2: float

    @property
    def width(self) -> float:
        return self.x2 - self.x1

    @property
    def height(self) -> float:
        return self.y2 - self.y1


@dataclass(frozen=True)
class Keyframe:
    """Hộp tại một thời điểm (giây, tính từ đầu video)."""

    time: float
    box: Box
    # Đa giác 4 góc ((x, y) theo toạ độ #res) tại keyframe này (chữ nghiêng/xoay + chuyển động): hộp đen vẽ theo đa giác nội suy
    # giữa các keyframe, `box` vẫn là hộp thẳng đặt chữ trắng. None = keyframe này không có đa giác (dùng 4 góc của `box`).
    polygon: tuple[tuple[float, float], ...] | None = None


@dataclass
class Subtitle:
    """Một phụ đề.

    Bất biến: đúng MỘT trong hai điều sau đúng:
      - `keyframes` không rỗng (đã sắp theo thời gian, không trùng giờ) và `box` là None;
      - `keyframes` rỗng và `box` là hộp cố định.
    Nhờ vậy mô hình luôn ở dạng chuẩn và round-trip đọc -> ghi -> đọc cho kết quả y hệt.
    """

    index: int
    start: float
    end: float
    text: str
    box: Box | None = None
    keyframes: list[Keyframe] = field(default_factory=list)
    # Cỡ chữ TỐI ĐA (đơn vị toạ độ #res); chữ vẫn co vừa hộp nếu hộp nhỏ hơn. None = chỉ co vừa hộp như thường lệ. Chỉ do bước "phụ đề chính"
    # (core/maincap.py) đặt khi xuất; không ghi vào file .srtpx.
    font_px: float | None = None
    # Đa giác 4 góc ((x, y) theo toạ độ #res) của hộp che khi chữ gốc NGHIÊNG. Chỉ dùng với hộp cố định (không keyframe):
    # hộp đen vẽ theo đa giác này; `box` vẫn là hộp thẳng dùng đặt chữ trắng (và là hộp dự phòng cho bộ đọc cũ).
    polygon: tuple[tuple[float, float], ...] | None = None

    def validate(self) -> None:
        """Ném ValueError nếu vi phạm bất biến ở trên."""
        if self.keyframes and self.box is not None:
            raise ValueError("Phụ đề có keyframe thì không được có hộp cố định.")
        if not self.keyframes and self.box is None:
            raise ValueError("Phụ đề phải có hộp cố định hoặc ít nhất một keyframe.")
        if self.polygon is not None:
            if self.keyframes:
                raise ValueError("Phụ đề có keyframe thì không được có đa giác.")
            if len(self.polygon) != 4:
                raise ValueError("Đa giác phải có đúng 4 góc.")
        times = [k.time for k in self.keyframes]
        if times != sorted(times) or len(set(times)) != len(times):
            raise ValueError("Keyframe phải sắp theo thời gian và không trùng giờ.")

    @property
    def first_box(self) -> Box:
        """Hộp dùng khi ghi file: keyframe đầu nếu có, nếu không thì hộp cố định."""
        if self.keyframes:
            return self.keyframes[0].box
        assert self.box is not None
        return self.box


@dataclass
class SrtpxDocument:
    """Toàn bộ file: độ phân giải đo toạ độ (nếu có) và danh sách phụ đề."""

    res: tuple[int, int] | None = None
    subtitles: list[Subtitle] = field(default_factory=list)


@dataclass(frozen=True)
class ParseWarning:
    """Một cảnh báo/lỗi khi đọc file.

    `line` là số dòng (tính từ 1) trong file; `block_skipped` là True nếu cả block bị bỏ.
    """

    line: int
    message: str
    block_skipped: bool = False

    def __str__(self) -> str:
        prefix = "Bỏ qua block" if self.block_skipped else "Cảnh báo"
        return f"{prefix} (dòng {self.line}): {self.message}"


@dataclass
class ParseResult:
    document: SrtpxDocument
    warnings: list[ParseWarning] = field(default_factory=list)
