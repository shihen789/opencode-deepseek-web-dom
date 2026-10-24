"""三通道浏览器统一调度。

条例来源：用户 2026-10-11 下发的《三通道使用条例》。
优先级 N > T > P，同一时刻只有一个持有互斥锁。
"""
from .scheduler import Scheduler, Channel, MutexBusy, ChannelUnavailable
from .channels.base import BaseChannel

__all__ = ["Scheduler", "Channel", "MutexBusy", "ChannelUnavailable", "BaseChannel"]
