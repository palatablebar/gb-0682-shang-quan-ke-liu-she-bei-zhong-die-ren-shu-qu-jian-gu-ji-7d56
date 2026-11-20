"""取整口径：观测层保留精确值，公布层向外取整。

- 观测层（M1-M3）：分层抽样给出的客次可以是小数，原样保留，交集与
  可行性都在精确值上判断，绝不逐条取整。
- 公布层（M5-M7）：对外签认的上下界必须是整数。下界向下取整、上界
  向上取整，保证公布区间仍然包住原始估计，且不会因为区间宽度不足 1
  而产生空区间（少量客流因此不会被隔离）。
- 原始估计以 raw_lower/raw_upper 随计算证据一并封存，审计可按
  floor/ceil 规则从公布值回到原始估计。
"""
from decimal import Decimal, ROUND_FLOOR, ROUND_CEILING

POLICY = 'PUBLISH_OUTWARD_V1'
DESCRIPTION = '观测层保留抽样小数；公布层下界向下取整、上界向上取整'


def exact(value):
    """把 int/Decimal/字符串统一成 Decimal，避免浮点误差。"""
    if isinstance(value, Decimal):
        return value
    return Decimal(str(value))


def normalize_number(value):
    """整数值保持 int（兼容既有输出），小数值保持 Decimal 精确表示。"""
    number = exact(value)
    if number == number.to_integral_value():
        return int(number)
    return number


def decimal_text(value):
    """稳定的十进制文本，用于证据与幂等摘要。"""
    number = exact(value).normalize()
    if number == number.to_integral_value():
        return str(int(number))
    return format(number, 'f')


def publish_lower(value):
    return int(exact(value).to_integral_value(rounding=ROUND_FLOOR))


def publish_upper(value):
    return int(exact(value).to_integral_value(rounding=ROUND_CEILING))


def publish_interval(lower, upper):
    """公布层向外取整。恒有 low <= high，不会凭空产生空区间。"""
    low, high = publish_lower(lower), publish_upper(upper)
    if low > high:  # 只有原始下界已高于上界才可能发生
        raise ValueError('interval has no width')
    return low, high
