import os
import subprocess
import time
import threading
from queue import Queue
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

# ========== Button Debouncing ==========
DEBOUNCE_TIME = 0.3
last_button_press = {CAPTURE_BUTTON: 0, PAUSE_BUTTON: 0, STOP_BUTTON: 0}

def button_pressed(pin):
    """Check if button is pressed with debouncing"""
    current_time = time.time()
    if (current_time - last_button_press[pin]) > DEBOUNCE_TIME:
        if GPIO.input(pin) == GPIO.LOW:
            last_button_press[pin] = current_time
            return True
    return False

# ========== GPIO Setup ==========
GPIO.setmode(GPIO.BCM)
GPIO.setup(CAPTURE_BUTTON, GPIO.IN, pull_up_down=GPIO.PUD_UP)
GPIO.setup(PAUSE_BUTTON, GPIO.IN, pull_up_down=GPIO.PUD_UP)
GPIO.setup(STOP_BUTTON, GPIO.IN, pull_up_down=GPIO.PUD_UP)

# ========== Camera Setup ==========
picam2 = None

def initialize_camera():
    """Initialize camera with error handling"""
    global picam2
    try:
        picam2 = Picamera2()
        # Use lower resolution for faster processing
        picam2.configure(picam2.create_still_configuration(main={"size": (800, 600)}))
        print("Camera initialized successfully")
        return True
    except Exception as e:
        print(f"Camera initialization failed: {e}")
        return False

# ========== Global State ==========
class AppState:
    def __init__(self):
        self.is_reading = False
        self.is_paused = False
        self.stop_reading = False
        self.current_process = None
        self.reading_thread = None
        self.start_time = None
        
    def reset_reading_state(self):
        self.is_reading = False
        self.is_paused = False
        self.stop_reading = False
        if self.current_process:
            try:
                self.current_process.terminate()
                self.current_process.wait(timeout=2)
            except:
                pass
            self.current_process = None

state = AppState()

# ========== Optimized Text-to-Speech ==========
def create_audio_file(text, filename="speech.mp3"):
    """Create audio file with error handling"""
    try:
        tts = gTTS(text=text, lang='en', slow=False)
        tts.save(filename)
        return True
    except Exception as e:
        print(f"TTS Error: {e}")
        return False

def play_audio_file(filename="speech.mp3"):
    """Play audio file non-blocking"""
    try:
        # Use aplay for better performance on Pi
        process = subprocess.Popen(
            ['aplay' if filename.endswith('.wav') else 'mpg321', filename], 
            stdout=subprocess.DEVNULL, 
            stderr=subprocess.DEVNULL
        )
        return process
    except Exception as e:
        print(f"Audio playback error: {e}")
        return None

def read_text_threaded(text):
    """Read text in a separate thread with proper button handling"""
    def read_worker():
        state.is_reading = True
        state.stop_reading = False
        state.start_time = time.time()
        
        # Split text into sentences for better control
        sentences = [s.strip() + '.' for s in text.split('.') if s.strip()]
        
        for i, sentence in enumerate(sentences):
            # Check for 10-minute limit
            if time.time() - state.start_time > 600:
                print("10-minute reading session ended.")
                break
                
            if state.stop_reading:
                break
                
            print(f"Speaking sentence {i+1}/{len(sentences)}: {sentence[:50]}...")
            
            # Create audio file
            if not create_audio_file(sentence):
                continue
                
            # Play audio
            state.current_process = play_audio_file()
            if not state.current_process:
                continue
                
            # Monitor playback and buttons
            while state.current_process and state.current_process.poll() is None:
                time.sleep(0.1)
                
                if state.stop_reading:
                    state.current_process.terminate()
                    break
                    
                if state.is_paused:
                    state.current_process.terminate()
                    # Wait for resume
                    while state.is_paused and not state.stop_reading:
                        time.sleep(0.1)
                    if not state.stop_reading:
                        # Resume from current sentence
                        state.current_process = play_audio_file()
                        if not state.current_process:
                            break
            
            # Clean up audio file
            try:
                os.remove("speech.mp3")
            except:
                pass
                
        state.reset_reading_state()
        print("Reading completed.")
    
    if state.reading_thread and state.reading_thread.is_alive():
        state.stop_reading = True
        state.reading_thread.join(timeout=2)
    
    state.reading_thread = threading.Thread(target=read_worker, daemon=True)
    state.reading_thread.start()

def quick_feedback(message):
    """Provide quick audio feedback without blocking"""
    print(f"Feedback: {message}")
    if create_audio_file(message, "feedback.mp3"):
        process = play_audio_file("feedback.mp3")
        if process:
            # Don't wait for completion, just start it
            threading.Thread(target=lambda: (process.wait(), os.remove("feedback.mp3")), daemon=True).start()

# ========== File Management ==========
def delete_existing_files(directory):
    """Clean up old files"""
    try:
        for filename in os.listdir(directory):
            file_path = os.path.join(directory, filename)
            if os.path.isfile(file_path):
                os.remove(file_path)
    except Exception as e:
        print(f"Error cleaning directory: {e}")

# ========== Optimized Image Capture ==========
def capture_image_fast(directory):
    """Fast image capture with immediate feedback"""
    filename = os.path.join(directory, 'captured_image.jpg')
    
    try:
        if not picam2:
            quick_feedback("Camera not available.")
            return None
            
        # Quick capture
        picam2.start()
        time.sleep(1)  # Reduced warm-up time
        
        # Capture as numpy array for faster processing
        frame = picam2.capture_array()
        picam2.stop()
        
        # Convert BGR to RGB for PIL
        frame_rgb = cv2.cvtColor(frame, cv2.COLOR_BGR2RGB)
        
        # Save image
        cv2.imwrite(filename, frame)
        
        quick_feedback("Image captured successfully.")
        return filename
        
    except Exception as e:
        print(f"Capture error: {e}")
        quick_feedback("Image capture failed.")
        return None

# ========== Optimized Image Processing ==========
def process_image_fast(chat_session, image_path):
    """Process image with optimizations"""
    try:
        # Open and potentially resize image for faster processing
        image = Image.open(image_path)
        
        # Resize if too large (speeds up API calls)
        max_size = 1024
        if max(image.size) > max_size:
            ratio = max_size / max(image.size)
            new_size = tuple(int(dim * ratio) for dim in image.size)
            image = image.resize(new_size, Image.Resampling.LANCZOS)
        
        image = image.convert("RGB")
        
        quick_feedback("Processing image, please wait.")
        
        # Send to Gemini API
        response = chat_session.send_message([
            image,
            "You are an AI for a reading device for visually impaired users. "
            "Extract ALL readable text from this image in the correct reading order. "
            "Return ONLY the extracted text, no additional commentary."
        ])
        
        if response and response.text:
            extracted_text = response.text.replace("*", "").strip()
            if extracted_text:
                print(f"Extracted text: {extracted_text[:100]}...")
                read_text_threaded(extracted_text)
            else:
                quick_feedback("No readable text found in the image.")
        else:
            quick_feedback("Could not process the image.")
            
    except Exception as e:
        print(f"Processing error: {e}")
        quick_feedback(f"Processing failed: {str(e)}")

# ========== Button Handlers ==========
def handle_capture_button():
    """Handle capture button press"""
    directory = 'image_to_examine'
    os.makedirs(directory, exist_ok=True)
    
    delete_existing_files(directory)
    image_path = capture_image_fast(directory)
    
    if image_path:
        return image_path
    return None

def handle_pause_button():
    """Handle pause/resume button"""
    if state.is_reading:
        state.is_paused = not state.is_paused
        if state.is_paused:
            quick_feedback("Reading paused.")
        else:
            quick_feedback("Reading resumed.")
    else:
        quick_feedback("No active reading to pause.")

def handle_stop_button():
    """Handle stop button"""
    if state.is_reading:
        state.stop_reading = True
        quick_feedback("Reading stopped.")
    else:
        quick_feedback("No active reading to stop.")

# ========== Main Loop ==========
def main():
    # Initialize API
    api_key = "AIzaSyC7vnVvZM1pnZ0s0vWF-uLnwwFa596uK-s"  # Replace with your valid key
    try:
        genai.configure(api_key=api_key)
        model = genai.GenerativeModel("gemini-1.5-flash")
        chat_session = model.start_chat(history=[])
        print("AI model initialized successfully")
    except Exception as e:
        print(f"AI initialization failed: {e}")
        return
    
    # Initialize camera
    if not initialize_camera():
        print("Failed to initialize camera. Exiting.")
        return
    
    # Startup feedback
    quick_feedback("Text reader ready. Press capture button to take a picture.")
    
    try:
        print("Text reader started. Press Ctrl+C to exit.")
        print("Buttons: Capture (GPIO 17), Pause/Resume (GPIO 22), Stop (GPIO 27)")
        
        while True:
            # Check capture button
            if button_pressed(CAPTURE_BUTTON):
                print("Capture button pressed")
                image_path = handle_capture_button()
                if image_path:
                    process_image_fast(chat_session, image_path)
            
            # Check pause button
            elif button_pressed(PAUSE_BUTTON):
                print("Pause button pressed")
                handle_pause_button()
            
            # Check stop button
            elif button_pressed(STOP_BUTTON):
                print("Stop button pressed")
                handle_stop_button()
            
            time.sleep(0.05)  # Small delay to prevent excessive CPU usage
            
    except KeyboardInterrupt:
        print("\nShutting down...")
        state.stop_reading = True
        quick_feedback("Shutting down text reader.")
        time.sleep(2)  # Allow feedback to play
        
    finally:
        # Cleanup
        state.reset_reading_state()
        if picam2:
            try:
                picam2.stop()
                picam2.close()
            except:
                pass
        GPIO.cleanup()
        print("Cleanup completed.")

if __name__ == "__main__":
    main()
