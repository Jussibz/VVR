import os
import subprocess
import time
import RPi.GPIO as GPIO
from PIL import Image
import google.generativeai as genai
from picamera2 import Picamera2
import cv2
import signal
import re
from gtts import gTTS

# ========== GPIO Pins ==========
CAPTURE_BUTTON = 17        # Start/Capture
ESC_BUTTON = 27            # Cancel/Stop
PAUSE_PLAY_BUTTON = 22     # Pause/Play

# ========== GPIO Setup ==========
GPIO.setmode(GPIO.BCM)
GPIO.setup(CAPTURE_BUTTON, GPIO.IN, pull_up_down=GPIO.PUD_UP)
GPIO.setup(ESC_BUTTON, GPIO.IN, pull_up_down=GPIO.PUD_UP)
GPIO.setup(PAUSE_PLAY_BUTTON, GPIO.IN, pull_up_down=GPIO.PUD_UP)

# ========== Camera Setup ==========
try:
    picam2 = Picamera2()
    picam2.configure(picam2.create_still_configuration())
except Exception as e:
    print(f"Camera initialization error: {e}")
    GPIO.cleanup()
    exit(1)

# ========== Persistent Progress ==========
PROGRESS_FILE = "reading_progress.txt"

# ========== Text-to-Speech ==========
def speak(text):
    try:
        tts = gTTS(text)
        tts.save("speech.mp3")
        process = subprocess.Popen(['mpg321', 'speech.mp3'], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        return process
    except Exception as e:
        print(f"TTS error: {e}")
        return None

# ========== Pause/Play Handler ==========
def handle_pause_play(process):
    paused = False
    while process and process.poll() is None:
        if GPIO.input(ESC_BUTTON) == GPIO.LOW:
            print("Reading interrupted.")
            speak("Cancelled")
            process.terminate()
            return "stop"

        if GPIO.input(PAUSE_PLAY_BUTTON) == GPIO.LOW:
            if not paused:
                print("Paused.")
                speak("Paused")
                process.send_signal(signal.SIGSTOP)
                paused = True
            else:
                print("Resumed.")
                speak("Resumed")
                process.send_signal(signal.SIGCONT)
                paused = False
            time.sleep(0.5)

        time.sleep(0.1)

    return "done"

# ========== Read Text ==========
def read_text(text):
    sentences = re.split(r'(?<=[.!?]) +', text.strip())
    index = 0

    if os.path.exists(PROGRESS_FILE):
        with open(PROGRESS_FILE, 'r') as f:
            saved = f.read()
            if saved.isdigit():
                index = int(saved)

    while index < len(sentences):
        sentence = sentences[index].strip()
        if not sentence:
            index += 1
            continue

        if GPIO.input(ESC_BUTTON) == GPIO.LOW:
            print("Reading aborted.")
            speak("Cancelled")
            return

        print(f"Speaking: {sentence}")
        process = speak(sentence)
        if not process:
            return

        result = handle_pause_play(process)
        if result == "stop":
            return

        index += 1
        with open(PROGRESS_FILE, 'w') as f:
            f.write(str(index))

    if os.path.exists(PROGRESS_FILE):
        os.remove(PROGRESS_FILE)

# ========== Delete Old Images ==========
def delete_existing_files(directory):
    if not os.path.exists(directory):
        os.makedirs(directory)
    for filename in os.listdir(directory):
        file_path = os.path.join(directory, filename)
        if os.path.isfile(file_path):
            os.remove(file_path)

# ========== Capture Image ==========
def capture_image(directory):
    read_text("Press the capture button to take a picture.")
    filename = os.path.join(directory, 'image.jpg')

    while True:
        if GPIO.input(ESC_BUTTON) == GPIO.LOW:
            read_text("Capture cancelled.")
            return None

        if GPIO.input(CAPTURE_BUTTON) == GPIO.LOW:
            delete_existing_files(directory)
            try:
                picam2.start()
                time.sleep(1.5)
                frame = picam2.capture_array()
                cv2.imwrite(filename, frame)
                picam2.stop()
                read_text("Image captured. Processing now.")
                return filename
            except Exception as e:
                read_text(f"Camera error: {str(e)}")
                return None

        time.sleep(0.1)

# ========== Process Image ==========
def process_image(chat_session, directory):
    if GPIO.input(ESC_BUTTON) == GPIO.LOW:
        read_text("Operation cancelled before capture.")
        return

    image_path = capture_image(directory)
    if not image_path:
        return

    if GPIO.input(ESC_BUTTON) == GPIO.LOW:
        read_text("Operation cancelled before processing.")
        return

    try:
        image = Image.open(image_path).convert("RGB")
        attempts = 3
        response = None

        for attempt in range(attempts):
            if GPIO.input(ESC_BUTTON) == GPIO.LOW:
                read_text("Operation cancelled during extraction.")
                return

            try:
                response = chat_session.send_message([
                    image,
                    "Assume you are an AI designed for an assistive reading device for the visually impaired. "
                    "Extract the exact text in this image. Do NOT add any additional words."
                ])
                if response and response.text:
                    break
            except Exception:
                if attempt == attempts - 1:
                    read_text("Image processing failed after three attempts.")
                    return
                time.sleep(1)

        if GPIO.input(ESC_BUTTON) == GPIO.LOW:
            read_text("Operation cancelled before reading.")
            return

        extracted_text = response.text.replace("*", "") if response and response.text else "No readable text found."
        read_text(extracted_text)

    except Exception as e:
        read_text(f"An error occurred: {str(e)}")

# ========== Main ==========
def main():
    api_key = "AIzaSyC7vnVvZM1pnZ0s0vWF-uLnwwFa596uK-s"
    genai.configure(api_key=api_key)

    directory = 'image_to_examine'
    os.makedirs(directory, exist_ok=True)

    model = genai.GenerativeModel("gemini-1.5-flash")
    chat_session = model.start_chat(history=[])

    try:
        while True:
            print("Waiting for new image capture...")
            process_image(chat_session, directory)
    except KeyboardInterrupt:
        print("Exiting...")
    finally:
        GPIO.cleanup()

# ========== Entry ==========
if _name_ == "_main_":
    try:
        main()
    except Exception as e:
        print(f"Unhandled error: {e}")
        GPIO.cleanup()
