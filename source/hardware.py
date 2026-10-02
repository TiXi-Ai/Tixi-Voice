# SPDX-License-Identifier: GPL-3.0-or-later
"""Conservative resource planning; never requires a GPU or user hardware configuration."""
from __future__ import annotations
import os
import struct
import ctypes
from dataclasses import dataclass,asdict
from pathlib import Path

@dataclass
class Resources:
    total_mb: int
    available_mb: int
    cores: int
    windows: bool
    bits: int
    wine: bool=False
    def to_dict(self):return asdict(self)

def resources():
    total=available=0;wine=False
    if os.name=='nt':
        class MEMORYSTATUSEX(ctypes.Structure):
            _fields_=[('dwLength',ctypes.c_uint32),('dwMemoryLoad',ctypes.c_uint32)]+[(n,ctypes.c_uint64) for n in ['ullTotalPhys','ullAvailPhys','ullTotalPageFile','ullAvailPageFile','ullTotalVirtual','ullAvailVirtual','ullAvailExtendedVirtual']]
        value=MEMORYSTATUSEX();value.dwLength=ctypes.sizeof(value)
        try:
            if ctypes.windll.kernel32.GlobalMemoryStatusEx(ctypes.byref(value)):
                total=int(value.ullTotalPhys)//1048576;available=int(value.ullAvailPhys)//1048576
        except (OSError,AttributeError):pass
        try:wine=hasattr(ctypes.WinDLL('ntdll'),'wine_get_version')
        except (OSError,AttributeError):pass
        # Wine is a test environment, not a supported hardware acceleration target.
        if wine:available=min(available or 1536,1536)
    else:
        try:
            fields={line.split(':')[0]:int(line.split()[1])//1024 for line in Path('/proc/meminfo').read_text().splitlines()}
            total=fields.get('MemTotal',0);available=fields.get('MemAvailable',0)
            limit=Path('/sys/fs/cgroup/memory.max').read_text().strip()
            used=int(Path('/sys/fs/cgroup/memory.current').read_text())
            if limit.isdigit():
                total=min(total,int(limit)//1048576);available=min(available,max(0,(int(limit)-used)//1048576))
        except (OSError,ValueError,IndexError):pass
    return Resources(total,available,max(1,os.cpu_count() or 1),os.name=='nt',struct.calcsize('P')*8,wine)

def plan(mode='auto',info=None,force_retry=False):
    r=info or resources()
    free=r.available_mb or 1024 # Unknown memory => conservative, not optimistic.
    low=(mode=='lite' or free<2000 or force_retry)
    seconds=1.5 if force_retry else 2.5 if low else 4.5 if free<6000 else 7.0
    threads=1 if low else min(4,max(1,r.cores//2))
    gpu=(mode=='auto' and not force_retry and r.windows and r.bits==64 and not r.wine and free>=2000)
    return dict(mode=mode,threads=threads,chunk_seconds=seconds,try_gpu=gpu,
                low_memory=low,insufficient=(r.available_mb>0 and r.available_mb<650),resources=r.to_dict())
