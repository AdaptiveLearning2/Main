import argparse
import threading
import time

import cv2
import rppg

# config
COLOR_GREEN = (0, 255, 0)
COLOR_RED = (0, 0, 255)
COLOR_YELLOW = (0, 255, 255)
MIN_SIGNAL_SECONDS = 60
RMSSD_UPDATE_INTERVAL = 1  # seconds between RMSSD recalculations
SIGNAL_WINDOW_SECONDS = 60  # only use the last N seconds of signal


# Suppress harmless rppg thread error 
def _suppress_rppg_thread_error(args):
    if args.exc_type is RuntimeError and "cannot join current thread" in str(args.exc_value):
        return
    threading.__excepthook__(args)


def lock_camera_settings(cap):
    """Lock auto-exposure and auto-white-balance to reduce signal noise."""
    cap.set(cv2.CAP_PROP_AUTO_EXPOSURE, 1)       # 1 = manual mode
    cap.set(cv2.CAP_PROP_AUTO_WB, 0)             # disable auto white balance
    cap.set(cv2.CAP_PROP_FPS, 30)                # match rppg model expectation
    cap.set(cv2.CAP_PROP_FRAME_WIDTH, 640)
    cap.set(cv2.CAP_PROP_FRAME_HEIGHT, 480)


def compute_rmssd(model, window_start):
    """Use the rppg library's built-in HR/HRV pipeline with SQI gating."""
    try:
        result = model.hr(start=window_start)
        if result is None:
            return None, None, None
        sqi = result.get('SQI')
        hr = result.get('hr')
        hrv = result.get('hrv', {})
        rmssd = hrv.get('rmssd') if hrv else None
        return rmssd, sqi, hr
    except Exception:
        return None, None, None


def parse_args():
    parser = argparse.ArgumentParser(description="Real-time rPPG RMSSD monitor")
    parser.add_argument(
        "--launch",
        choices=("window", "console"),
        default="window",
    )
    return parser.parse_args()


def main(launch="window"):
    threading.excepthook = _suppress_rppg_thread_error
    model = rppg.Model('RhythmMamba.rlap')
    model.face_detect_per_n = 1
    current_rmssd = current_sqi = current_hr = None
    last_update = 0
    last_console_status = 0

    with model.video_capture(0):
        # Lock camera settings to reduce auto-adjustment noise
        if hasattr(model, '_cap') and model._cap is not None:
            lock_camera_settings(model._cap)

        start_time = time.time()
        if launch == "window":
            print("Starting real-time rPPG. Press 'q' to quit.")
        else:
            print("Starting real-time rPPG in console mode. Press Ctrl+C to quit.")

        try:
            for frame, box in model.preview:
                elapsed = time.time() - start_time
                frame = cv2.cvtColor(frame, cv2.COLOR_RGB2BGR)

                if box is not None and launch == "window":
                    y1, y2 = box[0]
                    x1, x2 = box[1]
                    cv2.rectangle(frame, (x1, y1), (x2, y2), COLOR_GREEN, 2)

                # Periodically recompute RMSSD once enough signal exists
                if elapsed > MIN_SIGNAL_SECONDS and (time.time() - last_update) >= RMSSD_UPDATE_INTERVAL:
                    window_start = max(0, elapsed - SIGNAL_WINDOW_SECONDS)
                    rmssd, sqi, hr = compute_rmssd(model, window_start)
                    if sqi is not None:
                        current_sqi = sqi
                    if hr is not None:
                        current_hr = hr
                    if rmssd is not None:
                        current_rmssd = rmssd
                    last_update = time.time()

                if (time.time() - last_console_status) >= RMSSD_UPDATE_INTERVAL:
                    sqi_text = f"{current_sqi:.2f}" if current_sqi is not None else "n/a"
                    hr_text = f"{current_hr:.0f}" if current_hr is not None else "n/a"
                    if current_rmssd is not None:
                        print(f"RMSSD: {current_rmssd:.1f} ms | SQI: {sqi_text} | HR: {hr_text} bpm")
                    else:
                        secs_left = max(0, int(MIN_SIGNAL_SECONDS - elapsed))
                        if secs_left > 0:
                            print(f"Collecting signal... {secs_left}s")
                        else:
                            print(f"RMSSD: N/A | SQI: {sqi_text} (Bad SQI) | HR: {hr_text} bpm")
                    last_console_status = time.time()

                if launch == "window":
                    cv2.imshow("rPPG Real-Time RMSSD", frame)
                    if cv2.waitKey(1) & 0xFF == ord("q"):
                        break
        except KeyboardInterrupt:
            print("\nStopped by user.")

    if launch == "window":
        cv2.destroyAllWindows()

    if current_rmssd is not None:
        print(f"\nFinal RMSSD: {current_rmssd:.1f} ms")
    else:
        print("\nNot enough signal captured to compute RMSSD.")


if __name__ == "__main__":
    args = parse_args()
    main(launch=args.launch)
