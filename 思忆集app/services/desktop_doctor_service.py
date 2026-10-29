"""Proxy module for backward compatibility.

Re-exports from desktop.doctor_service so import paths
continue to work after the directory restructure.

Usage:
    from services.desktop_doctor_service import DesktopDoctorService
"""

from desktop.doctor_service import DesktopDoctorService  # noqa: E402, F401
