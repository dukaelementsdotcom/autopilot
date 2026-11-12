"""one-line test of the side gate: a long is rejected while its side is off and accepted once its condition is met"""
from .referee import side_live
def test():
    assert side_live({}, "long", {"off": True, "live": False}, "up-calm") is False and side_live({"regimeOverride": {"long": {"up-calm": {"live": True}}}}, "long", {"off": True, "live": False}, "up-calm") is True
if __name__ == "__main__": test(); print("side gate test passed")
