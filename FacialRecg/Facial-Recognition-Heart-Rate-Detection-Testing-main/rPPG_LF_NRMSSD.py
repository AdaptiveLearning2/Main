import threading
import time

import cv2
import rppg

# config
HR_UPDATE_INTERVAL = 1.0  # seconds between RMSSD recalculations

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

def main():
    threading.excepthook = _suppress_rppg_thread_error
    model = rppg.Model('RhythmMamba.rlap')
    model.face_detect_per_n = 1
    current_hr = None
    last_update = 0

    with model.video_capture(0):
        # Lock camera settings to reduce auto-adjustment noise
        if hasattr(model, '_cap') and model._cap is not None:
            lock_camera_settings(model._cap)
            
            print("Starting real-time HR. Press Ctrl+C to quit.")

        try:
            while model.preview:
                
                now = time.time()
                if now - last_update > HR_UPDATE_INTERVAL:
                    result = model.hr(start=-10)
                    if result and result["hr"]:
                        current_hr = result["hr"]
                        print(f"Real-time HR: {current_hr:.1f} BPM")
                    else:
                        print("Real-time HR: N/A")
                    last_update = now

        except KeyboardInterrupt:
            print("Stopped by user.")

if __name__ == "__main__":
    main()