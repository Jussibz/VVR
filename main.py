import os
import subprocess
import time
import RPi.GPIO as GPIO
from PIL import Image
import google.generativeai as genai
from picamera2 import Picamera2
import cv2
from gtts import gTTS

# ========== GPIO Pins ==========
CAPTURE_BUTTON = 17      # Left green - Capture image
PAUSE_BUTTON = 22        # Middle green - Pause/Resume
STOP_BUTTON = 27         # Red - Stop reading

GPIO.setmode(GPIO.BCM)
GPIO.setup(CAPTURE_BUTTON, GPIO.IN, pull_up_down=GPIO.PUD_UP)
GPIO.setup(PAUSE_BUTTON, GPIO.IN, pull_up_down=GPIO.PUD_UP)
GPIO.setup(STOP_BUTTON, GPIO.IN, pull_up_down=GPIO.PUD_UP)

# ========== Camera Setup ==========
picam2 = Picamera2()
picam2.configure(picam2.create_still_configuration(main={"size": (1024, 768)}))

# ========== Flags and Timers ==========
is_paused = False
start_time = None

# ========== Text-to-Speech ==========
def read_text(text):
    global is_paused, start_time
    sentences = text.split('. ')
    index = 0
    start_time = time.time()

    while index < len(sentences):
        if time.time() - start_time > 600:
            print("10-minute reading session ended.")
            return

        sentence = sentences[index].strip()
        if not sentence:
            index += 1
            continue

        print(f"Speaking: {sentence}")
        try:
            tts = gTTS(sentence)
            tts.save("speech.mp3")
            process = subprocess.Popen(['mpg321', 'speech.mp3'], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        except Exception as e:
            print(f"TTS Error: {e}")
            return

        while process.poll() is None:
            if GPIO.input(STOP_BUTTON) == GPIO.LOW:
                print("Reading stopped.")
                process.terminate()
                return
            elif GPIO.input(PAUSE_BUTTON) == GPIO.LOW:
                print("Reading paused.")
                is_paused = True
                process.terminate()
                while is_paused:
                    if GPIO.input(PAUSE_BUTTON) == GPIO.LOW:
                        print("Reading resumed.")
                        is_paused = False
                        break
                    time.sleep(0.1)
                break
            time.sleep(0.1)
        else:
            index += 1

# ========== Delete Old Images ==========
def delete_existing_files(directory):
    for filename in os.listdir(directory):
        os.remove(os.path.join(directory, filename))

# ========== Capture Image ==========
def capture_image(directory):
    read_text("Press the capture button to take a picture.")
    filename = os.path.join(directory, 'image.jpg')

    while True:
        if GPIO.input(CAPTURE_BUTTON) == GPIO.LOW:
            delete_existing_files(directory)
            picam2.start()
            time.sleep(2)
            frame = picam2.capture_array()
            picam2.stop()
            cv2.imwrite(filename, frame)
            read_text("Image captured. Processing now.")
            return filename

        elif GPIO.input(STOP_BUTTON) == GPIO.LOW:
            read_text("Capture cancelled.")
            return None

        time.sleep(0.1)

# ========== Process Image ==========
def process_image(chat_session, directory):
    image_path = capture_image(directory)
    if not image_path:
        return

    try:
        image = Image.open(image_path).convert("RGB")
        response = chat_session.send_message([
            image,
            "Assume you are an AI designed for an assistive reading device for the visually impaired. "
            "Extract the exact text in this image. Do NOT add any additional words."
        ])
        extracted_text = response.text.replace("*", "") if response and response.text else "No readable text found."
        read_text(extracted_text)
    except Exception as e:
        read_text(f"An error occurred: {str(e)}")

# ========== Main ==========
def main():
    api_key = "AIzaSyC7vnVvZM1pnZ0s0vWF-uLnwwFa596uK-s"  # Replace with your valid key
    genai.configure(api_key=api_key)

    directory = 'image_to_examine'
    os.makedirs(directory, exist_ok=True)

    model = genai.GenerativeModel("gemini-1.5-flash")
    chat_session = model.start_chat(history=[])

    try:
        while True:
            process_image(chat_session, directory)
    except KeyboardInterrupt:
        print("Exiting...")
    finally:
        GPIO.cleanup()

if __name__ == "__main__":
    main()

