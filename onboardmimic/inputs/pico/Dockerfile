# Build ON the robot (ARM64), not inside the existing SONIC environment.
FROM ubuntu:22.04
ENV DEBIAN_FRONTEND=noninteractive PYTHONUNBUFFERED=1
RUN apt-get update && apt-get install -y --no-install-recommends python3 python3-pip python3-dev build-essential binutils ca-certificates libglib2.0-0 libdbus-1-3 libicu70 libssl3 libzstd1 zlib1g libdouble-conversion3 libpcre2-16-0 libb2-1 libxkbcommon0 libxcb1 libx11-6 libgl1 libegl1 libfontconfig1 libfreetype6 libpulse0 && rm -rf /var/lib/apt/lists/*
WORKDIR /app
COPY dependencies.json fetch_dependencies.py patch_sdk.py receiver.py /app/
COPY vendor/XRoboToolkit-PC-Service-headless_1.0.0.0_arm64.deb vendor/xrt-python-8941e1d.tar.gz /app/vendor/
RUN test "$(uname -m)" = aarch64 && python3 fetch_dependencies.py && mkdir -p /opt/xrt /opt/sdk-src && dpkg-deb -x /app/vendor/*.deb /opt/xrt && tar -xzf /app/vendor/xrt-python-8941e1d.tar.gz --strip-components=1 -C /opt/sdk-src && python3 patch_sdk.py /opt/sdk-src/bindings/py_bindings.cpp && python3 -m pip install --no-cache-dir /opt/sdk-src
ENV LD_LIBRARY_PATH=/opt/xrt/opt/apps/roboticsservice:/opt/xrt/opt/apps/roboticsservice/lib:/opt/xrt/opt/apps/roboticsservice/SDK/arm64
ENV QT_QPA_PLATFORM=offscreen
RUN python3 -c 'import xrobotoolkit_sdk'
RUN ldd /opt/xrt/opt/apps/roboticsservice/RoboticsServiceProcess > /tmp/xrt-ldd.txt && cat /tmp/xrt-ldd.txt && ! grep 'not found' /tmp/xrt-ldd.txt
COPY container_capture.sh /app/
# Default never starts a receiver, controller or service.
CMD ["python3", "receiver.py", "--help"]
