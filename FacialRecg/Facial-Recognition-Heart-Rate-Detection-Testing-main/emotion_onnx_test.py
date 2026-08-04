import argparse
import os
import time
import urllib.request

import cv2
import numpy as np
import onnxruntime as ort


MODEL_URL = (
    "https://huggingface.co/onnxmodelzoo/emotion-ferplus-8/"
    "resolve/main/emotion-ferplus-8.onnx"
)
DEFAULT_MODEL_PATH = "emotion-ferplus-8.onnx"

# FER+ output order used by the ONNX Model Zoo model.
EMOTIONS = [
    "neutral",
    "happy",
    "surprise",
    "sad",
    "angry",
    "disgust",
    "fear",
    "contempt",
]


def download_model(model_path: str) -> None:
    if os.path.exists(model_path):
        return

    print(f"Downloading emotion model to {model_path}...")
    urllib.request.urlretrieve(MODEL_URL, model_path)
    print("Model downloaded.")


def softmax(values: np.ndarray) -> np.ndarray:
    values = values - np.max(values)
    exp_values = np.exp(values)
    return exp_values / np.sum(exp_values)


def prepare_face(face_bgr: np.ndarray) -> np.ndarray:
    """Convert a face crop into the FER+ N x 1 x 64 x 64 input."""
    gray = cv2.cvtColor(face_bgr, cv2.COLOR_BGR2GRAY)
    gray = cv2.resize(gray, (64, 64), interpolation=cv2.INTER_AREA)
    tensor = gray.astype(np.float32)
    tensor = np.expand_dims(tensor, axis=0)
    tensor = np.expand_dims(tensor, axis=0)
    return tensor


def parse_args():
    parser = argparse.ArgumentParser(
        description="Simple webcam emotion-recognition test using FER+ ONNX."
    )
    parser.add_argument("--model", default=DEFAULT_MODEL_PATH)
    parser.add_argument("--camera", type=int, default=0)
    return parser.parse_args()


def main():
    args = parse_args()
    download_model(args.model)

    session = ort.InferenceSession(
        args.model,
        providers=["CPUExecutionProvider"],
    )
    input_name = session.get_inputs()[0].name

    cascade_path = os.path.join(
        cv2.data.haarcascades,
        "haarcascade_frontalface_default.xml",
    )
    face_detector = cv2.CascadeClassifier(cascade_path)
    if face_detector.empty():
        raise RuntimeError("OpenCV face detector could not be loaded.")

    camera = cv2.VideoCapture(args.camera)
    if not camera.isOpened():
        raise RuntimeError(f"Could not open camera {args.camera}.")

    last_inference = 0.0
    current_emotion = "unknown"
    current_confidence = 0.0

    print("Emotion test started. Press q to quit.")

    try:
        while True:
            ok, frame = camera.read()
            if not ok:
                print("Could not read camera frame.")
                break

            gray = cv2.cvtColor(frame, cv2.COLOR_BGR2GRAY)
            faces = face_detector.detectMultiScale(
                gray,
                scaleFactor=1.1,
                minNeighbors=5,
                minSize=(80, 80),
            )

            if len(faces) > 0:
                # Use the largest detected face.
                x, y, w, h = max(faces, key=lambda box: box[2] * box[3])

                # Add a small margin while keeping the crop inside the frame.
                margin_x = int(w * 0.08)
                margin_y = int(h * 0.08)
                x1 = max(0, x - margin_x)
                y1 = max(0, y - margin_y)
                x2 = min(frame.shape[1], x + w + margin_x)
                y2 = min(frame.shape[0], y + h + margin_y)
                face_crop = frame[y1:y2, x1:x2]

                # Run about 4 times per second to keep CPU use low.
                if face_crop.size and time.time() - last_inference >= 0.25:
                    tensor = prepare_face(face_crop)
                    output = session.run(None, {input_name: tensor})[0]
                    logits = np.asarray(output).reshape(-1)

                    if logits.size != len(EMOTIONS):
                        raise RuntimeError(
                            f"Expected {len(EMOTIONS)} emotion outputs, "
                            f"but model returned {logits.size}."
                        )

                    probabilities = softmax(logits)
                    index = int(np.argmax(probabilities))
                    current_emotion = EMOTIONS[index]
                    current_confidence = float(probabilities[index])
                    last_inference = time.time()

                cv2.rectangle(frame, (x1, y1), (x2, y2), (0, 255, 0), 2)
                label = f"{current_emotion}: {current_confidence:.0%}"
                cv2.putText(
                    frame,
                    label,
                    (x1, max(25, y1 - 10)),
                    cv2.FONT_HERSHEY_SIMPLEX,
                    0.75,
                    (0, 255, 0),
                    2,
                    cv2.LINE_AA,
                )
            else:
                cv2.putText(
                    frame,
                    "No face detected",
                    (20, 35),
                    cv2.FONT_HERSHEY_SIMPLEX,
                    0.75,
                    (0, 0, 255),
                    2,
                    cv2.LINE_AA,
                )

            cv2.imshow("Emotion Recognition Test", frame)
            if cv2.waitKey(1) & 0xFF == ord("q"):
                break
    finally:
        camera.release()
        cv2.destroyAllWindows()


if __name__ == "__main__":
    main()