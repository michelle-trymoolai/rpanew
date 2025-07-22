import time
import os
import logging
import requests
from selenium import webdriver
from selenium.webdriver.chrome.service import Service
from selenium.webdriver.chrome.options import Options
from selenium.webdriver.common.by import By
from selenium.webdriver.support.ui import WebDriverWait
from selenium.webdriver.support import expected_conditions as EC
from selenium.webdriver.common.keys import Keys
from selenium.webdriver.common.action_chains import ActionChains
import traceback
from datetime import datetime
import platform
import sys
import json
from dotenv import load_dotenv

# Load environment variables
load_dotenv()

# Get data from command line arguments (passed from app.py)
if len(sys.argv) > 1:
    data = json.loads(sys.argv[1])
    member_id = data.get('member_id', '')
    patient_dob = data.get('date_of_birth', '')
    patient_name = data.get('patient_name', '')
    provider_name = data.get('provider_name', '')
    provider_npi = data.get('npi_number', '')
    procedure_code = data.get('procedure_code', '')
    diagnosis_code = data.get('diagnosis_code', '')
    from_date = data.get('from_date', '')
    to_date = data.get('to_date', '')
    primary_insurance = data.get('primary_insurance', '')
   
    print(f"Received data - Member ID: {member_id}, DOB: {patient_dob}")
else:
    print("No input data provided.")
    sys.exit(1)

# ===============================
# ENVIRONMENT VARIABLES
# ===============================
EMAIL = os.getenv('AVAILITY_EMAIL', 'GabrielleP2025')
PASSWORD = os.getenv('AVAILITY_PASSWORD', 'Password12345#')
PROVIDER_NAME = os.getenv('DEFAULT_PROVIDER_NAME', 'KOLLIPARA, ANURADHA')
PLACE_OF_SERVICE = os.getenv('PLACE_OF_SERVICE', '11 - Office')
PROCEDURE_QUANTITY = os.getenv('PROCEDURE_QUANTITY', '1')
PROCEDURE_QUANTITY_TYPE = os.getenv('PROCEDURE_QUANTITY_TYPE', 'Days')

# MFA Integration Settings
FLASK_BASE_URL = "http://localhost:5000"  # Adjust if Flask runs on different port
mfa_session_id = None

def request_mfa_session():
    """Request a new MFA session from Flask backend"""
    global mfa_session_id
    try:
        response = requests.post(f"{FLASK_BASE_URL}/mfa-request", 
                               json={"script_type": "aetna_prior_auth"}, 
                               timeout=10)
        if response.status_code == 200:
            data = response.json()
            mfa_session_id = data.get('session_id')
            logger.info(f"MFA session requested: {mfa_session_id}")
            return True
        else:
            logger.error(f"Failed to request MFA session: {response.status_code}")
            return False
    except Exception as e:
        logger.error(f"Error requesting MFA session: {str(e)}")
        return False

def wait_for_mfa_code(timeout=300):
    """Poll Flask backend for MFA code"""
    global mfa_session_id
    if not mfa_session_id:
        logger.error("No MFA session ID available")
        return None
        
    start_time = time.time()
    while time.time() - start_time < timeout:
        try:
            response = requests.get(f"{FLASK_BASE_URL}/mfa-check/{mfa_session_id}", 
                                  timeout=10)
            if response.status_code == 200:
                data = response.json()
                if data.get('status') == 'completed':
                    mfa_code = data.get('code')
                    logger.info(f"Received MFA code: {mfa_code}")
                    return mfa_code
                elif data.get('status') == 'expired':
                    logger.error("MFA session expired")
                    return None
                    
            # Wait before next poll
            time.sleep(3)
            
        except Exception as e:
            logger.error(f"Error checking MFA code: {str(e)}")
            time.sleep(3)
            
    logger.error("Timeout waiting for MFA code")
    return None

def handle_mfa_challenge(driver):
    """Handle MFA challenge by requesting session and waiting for code"""
    logger.info("MFA challenge detected, requesting user input...")
    
    # Request MFA session
    if not request_mfa_session():
        logger.error("Failed to request MFA session")
        return False
        
    # Wait for user to enter code
    mfa_code = wait_for_mfa_code()
    if not mfa_code:
        logger.error("Failed to get MFA code")
        return False
        
    # Enter the code
    try:
        # Look for MFA input field
        mfa_input = WebDriverWait(driver, 10).until(
            EC.presence_of_element_located((By.XPATH, "//input[@placeholder='Code' or @name='code' or @type='text']"))
        )
        
        mfa_input.clear()
        mfa_input.send_keys(mfa_code)
        
        # Look for submit button
        submit_button = driver.find_element(By.XPATH, "//button[contains(text(), 'Continue') or contains(text(), 'Submit') or contains(text(), 'Verify')]")
        submit_button.click()
        
        logger.info("MFA code entered successfully")
        return True
        
    except Exception as e:
        logger.error(f"Error entering MFA code: {str(e)}")
        return False

# ===============================
# SETUP LOGGING
# ===============================
os.makedirs("logs", exist_ok=True)
os.makedirs("screenshots", exist_ok=True)

log_filename = f"logs/availity_bot_{datetime.now().strftime('%Y%m%d_%H%M%S')}.log"
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(levelname)s - %(message)s',
    handlers=[
        logging.FileHandler(log_filename),
        logging.StreamHandler()
    ]
)
logger = logging.getLogger("availity_bot")

# ===============================
# CONFIGURATION
# ===============================
TIMEOUT = 20
LONG_TIMEOUT = 60
VERY_LONG_TIMEOUT = 120

# Element locators
LOCATORS = {
    "login_link": (By.CSS_SELECTOR, "a[href='https://apps.availity.com/web/onboarding/availity-fr-ui/']"),
    "username_input": (By.ID, "userId"),
    "password_input": (By.ID, "password"),
    "sign_in_button": (By.XPATH, "//button[contains(text(), 'Sign In')]"),
    "sms_option": (By.XPATH, "//label[contains(., 'Authenticate me using my Authenticator app')]"),
    "continue_button": (By.XPATH, "//button[contains(text(), 'Continue')]"),
    "patient_registration": (By.XPATH, "//a[contains(text(), 'Patient Registration')]"),
    "auth_and_referrals": (By.XPATH, "//div[contains(@class, 'media-body') and contains(., 'Authorizations & Referrals')]"),
    "cookie_button": (By.XPATH, "//button[contains(text(), 'Accept All Cookies')]"),
    "new_request_button": (By.XPATH, "//button[contains(@class, 'btn-primary') and contains(text(), 'New Request')]"),
    "authorization_request_link": (By.ID, "navigation-authorizations"),
    "next_button": (By.XPATH, "//button[contains(text(), 'Next')]"),
    "chrome_save_password_no": (By.XPATH, "//button[text()='Never' or text()='No']"),
    "next_steps_button": (By.ID, "nextStepsButton"),
    "submit_button": (By.ID, "authWizardNextButton"),
    "final_new_request_button": (By.XPATH, "//button[contains(@class, 'btn-primary') and contains(text(), 'New Request')]"),
}

def format_date_for_form(date_str):
    """Format date string to MM/DD/YYYY format for form input"""
    if not date_str:
        return ''
   
    try:
        # If it's in YYYY-MM-DD format, convert to MM/DD/YYYY
        if '-' in date_str and len(date_str) == 10:
            parsed_date = datetime.strptime(date_str, '%Y-%m-%d')
            return parsed_date.strftime('%m/%d/%Y')
        # If it's already in MM/DD/YYYY format, return as is
        elif '/' in date_str:
            return date_str
        else:
            return date_str
    except:
        return date_str

def setup_chrome_driver():
    """Setup Chrome driver with automatic detection for Flask"""
    logger.info("🔧 Setting up Chrome driver...")
   
    system = platform.system()
    machine = platform.machine()
    logger.info(f"🖥️ Detected system: {system} {machine}")
   
    options = Options()
    # Removed headless mode - browser will be visible
    options.add_argument("--start-maximized")
    options.add_argument("--disable-extensions")
    options.add_argument("--disable-gpu")
    options.add_argument("--no-sandbox")
    options.add_argument("--disable-dev-shm-usage")
   
    driver = None
   
    # Try using webdriver-manager first
    try:
        from webdriver_manager.chrome import ChromeDriverManager
        logger.info("Using webdriver-manager...")
        service = Service(ChromeDriverManager().install())
        driver = webdriver.Chrome(service=service, options=options)
        logger.info("Chrome driver initialized successfully")
        return driver
    except ImportError:
        logger.warning("webdriver-manager not installed")
    except Exception as e:
        logger.warning(f"webdriver-manager failed: {str(e)}")
   
    # Try system ChromeDriver
    try:
        logger.info("Trying system ChromeDriver...")
        driver = webdriver.Chrome(options=options)
        logger.info("Chrome driver initialized successfully")
        return driver
    except Exception as e:
        logger.warning(f"System ChromeDriver failed: {str(e)}")
   
    # Try local chromedriver
    try:
        logger.info("Trying local chromedriver...")
        service = Service("./chromedriver")
        driver = webdriver.Chrome(service=service, options=options)
        logger.info("Chrome driver initialized successfully")
        return driver
    except Exception as e:
        logger.warning(f"Local chromedriver failed: {str(e)}")
   
    if not driver:
        logger.error("Failed to initialize Chrome driver")
        return None
   
    return driver

# ===============================
# HELPER FUNCTIONS
# ===============================
def take_screenshot(driver, name):
    """Take a screenshot with a timestamp and organize in folders"""
    timestamp = time.strftime("%Y%m%d-%H%M%S")
    filename = f"screenshots/{name}_{timestamp}.png"
    try:
        driver.save_screenshot(filename)
        logger.info(f"Saved screenshot: {filename}")
    except Exception as e:
        logger.error(f"Failed to take screenshot: {str(e)}")
    return filename

def wait_for_page_load(driver, timeout=TIMEOUT):
    """Enhanced page load waiting with multiple checks"""
    start_time = time.time()

    # Wait for readyState complete
    try:
        WebDriverWait(driver, timeout).until(
            lambda d: d.execute_script("return document.readyState") == "complete"
        )
        logger.info("Document ready state complete")
    except Exception as e:
        logger.warning(f"Timed out waiting for readyState: {str(e)}")

    # Additional buffer time
    time.sleep(2)
    logger.info(f"Total page load wait: {time.time() - start_time:.2f} seconds")

def safe_click(driver, element, description="element", max_attempts=3):
    """Safely click an element using multiple methods with retry logic"""
    logger.info(f" Attempting to click {description}...")

    # First scroll to the element and wait for it to be clickable
    try:
        driver.execute_script("arguments[0].scrollIntoView({block: 'center', behavior: 'smooth'});", element)
        time.sleep(1)  # Wait for scroll to complete
    except Exception as e:
        logger.warning(f"Failed to scroll to {description}: {str(e)}")

    methods = [
        {"name": "Regular click", "action": lambda: element.click()},
        {"name": "JavaScript click", "action": lambda: driver.execute_script("arguments[0].click();", element)},
        {"name": "ActionChains click", "action": lambda: ActionChains(driver).move_to_element(element).click().perform()},
    ]

    for attempt in range(max_attempts):
        for method in methods:
            try:
                method["action"]()
                logger.info(f"{method['name']} on {description} successful (attempt {attempt+1})")
                time.sleep(1)  # Wait for click to take effect
                return True
            except Exception as e:
                logger.debug(f" {method['name']} failed on attempt {attempt+1}: {str(e)}")
                continue

    logger.error(f" All click methods failed for {description} after {max_attempts} attempts")
    take_screenshot(driver, f"click_failed_{description.replace(' ', '_')}")
    return False

def wait_for_and_find_element(driver, locator, description="element", timeout=TIMEOUT):
    """Wait for an element to be present and return it"""
    logger.info(f" Looking for {description}...")
    try:
        element = WebDriverWait(driver, timeout).until(
            EC.presence_of_element_located(locator)
        )
        logger.info(f" Found {description}")
        return element
    except Exception as e:
        logger.error(f" Failed to find {description}: {str(e)}")
        take_screenshot(driver, f"element_not_found_{description.replace(' ', '_')}")
        return None

def navigate_to_url(driver, url, description="page"):
    """Navigate to a URL with enhanced error handling"""
    logger.info(f" Navigating to {description} at {url}")
    try:
        driver.get(url)
        wait_for_page_load(driver)
        logger.info(f" Successfully loaded {description}")
        return True
    except Exception as e:
        logger.error(f" Failed to navigate to {description}: {str(e)}")
        take_screenshot(driver, f"navigation_failed_{description.replace(' ', '_')}")
        return False

def handle_select2_field(driver, field_label, value_to_select):
    """
    Handle Select2 dropdown fields by properly targeting the search input that appears
    after clicking the dropdown
    """
    logger.info(f" Handling Select2 field for {field_label}...")
   
    try:
        # Find all select2-chosen elements (the dropdown triggers)
        select2_elements = driver.find_elements(By.CSS_SELECTOR, "span.select2-chosen")
        logger.info(f" Found {len(select2_elements)} select2-chosen elements")
       
        take_screenshot(driver, f"all_select2_elements_for_{field_label.replace(' ', '_').lower()}")
       
        # Find the dropdown for our field based on parent text
        field_element = None
        for i, element in enumerate(select2_elements):
            try:
                parent_text = driver.execute_script("return arguments[0].parentElement.parentElement.textContent || '';", element)
                logger.info(f" Element {i} parent text: {parent_text}")
               
                if field_label in parent_text:
                    field_element = element
                    logger.info(f" Found {field_label} dropdown with parent text: {parent_text}")
                    break
            except:
                logger.info(f" Couldn't get parent text for element {i}")
       
        # If we couldn't find it by parent text, try using JavaScript
        if not field_element:
            logger.info(f" Trying JavaScript to find {field_label} dropdown")
            field_element = driver.execute_script(f"""
                var labels = document.querySelectorAll('label');
                for (let label of labels) {{
                    if (label.textContent.includes('{field_label}')) {{
                        let container = label.closest('div').querySelector('.select2-container');
                        if (container) {{
                            return container.querySelector('span.select2-chosen') || container;
                        }}
                    }}
                }}
                return null;
            """)
       
        if not field_element:
            logger.error(f" Could not find dropdown for {field_label}")
            take_screenshot(driver, f"{field_label.replace(' ', '_').lower()}_dropdown_not_found")
            return False
       
        # Click the dropdown to open it
        safe_click(driver, field_element, f"{field_label} dropdown")
        logger.info(f" Clicked {field_label} dropdown")
        time.sleep(1)
        take_screenshot(driver, f"after_{field_label.replace(' ', '_').lower()}_dropdown_click")
       
        # Look for any visible search input
        try:
            logger.info(" Looking for any visible search input")
            search_inputs = driver.find_elements(By.CSS_SELECTOR, "input.select2-input, div.select2-search input")
           
            search_input = None
            for input_elem in search_inputs:
                if input_elem.is_displayed():
                    search_input = input_elem
                    logger.info(" Found visible search input")
                    break
        except Exception as e:
            logger.info(f" Could not find any visible search input: {str(e)}")
       
        # If we found the search input, type into it and press Enter
        if search_input:
            # Click to focus the input
            ActionChains(driver).move_to_element(search_input).click().perform()
            logger.info(" Clicked search input")
            time.sleep(0.5)
           
            # Clear any existing value
            search_input.clear()
           
            # Type the value with a delay between characters
            logger.info(f" Typing '{value_to_select}' in search field")
            for char in value_to_select:
                search_input.send_keys(char)
                time.sleep(0.2)  # Slower typing to ensure stability
           
            time.sleep(1)  # Wait for search results
            take_screenshot(driver, f"after_typing_{field_label.replace(' ', '_').lower()}")
           
            # Press Enter to select the first matching option
            search_input.send_keys(Keys.ENTER)
            logger.info(" Pressed Enter to select option")
            time.sleep(1)
            take_screenshot(driver, f"after_{field_label.replace(' ', '_').lower()}_selection")
            return True
        else:
            logger.error(f" Could not find search input for {field_label}")
            return False
           
    except Exception as e:
        logger.error(f" Error handling Select2 field for {field_label}: {str(e)}")
        take_screenshot(driver, f"{field_label.replace(' ', '_').lower()}_error")
        return False

# ===============================
# IMPROVED DROPDOWN SELECTION FUNCTIONS
# ===============================
def handle_select2_dropdown_with_selection(driver, field_label, value_to_select):
    """
    Improved function to handle Select2 dropdown fields with proper selection confirmation
    """
    logger.info(f" Handling Select2 dropdown for {field_label} with value: {value_to_select}")
   
    try:
        # Find the dropdown container
        dropdown_container = None
       
        # Method 1: Find by label text
        try:
            dropdown_container = driver.execute_script(f"""
                var labels = document.querySelectorAll('label');
                for (let label of labels) {{
                    if (label.textContent.includes('{field_label}')) {{
                        let container = label.closest('.form-group').querySelector('.select2-container');
                        if (container) return container;
                    }}
                }}
                return null;
            """)
            if dropdown_container:
                logger.info(f" Found {field_label} dropdown by label")
        except Exception as e:
            logger.info(f" Method 1 failed: {str(e)}")
       
        # Method 2: Find by ID pattern if Method 1 failed
        if not dropdown_container:
            try:
                field_id_pattern = field_label.lower().replace(" ", "").replace("code", "Code")
                dropdown_container = driver.find_element(By.CSS_SELECTOR, f"div.select2-container[id*='{field_id_pattern}']")
                logger.info(f" Found {field_label} dropdown by ID pattern")
            except Exception as e:
                logger.info(f" Method 2 failed: {str(e)}")
       
        if not dropdown_container:
            logger.error(f" Could not find dropdown container for {field_label}")
            return False
       
        # Step 1: Click to open the dropdown
        logger.info(f" Clicking {field_label} dropdown to open it")
        safe_click(driver, dropdown_container, f"{field_label} dropdown")
        time.sleep(2)  # Wait for dropdown to open
        take_screenshot(driver, f"after_{field_label.lower().replace(' ', '_')}_dropdown_click")
       
        # Step 2: Find the search input that appears after clicking
        search_input = None
        search_attempts = 0
        max_search_attempts = 3
       
        while not search_input and search_attempts < max_search_attempts:
            try:
                # Look for visible search inputs
                search_inputs = driver.find_elements(By.CSS_SELECTOR, "input.select2-input")
                for input_elem in search_inputs:
                    if input_elem.is_displayed() and input_elem.is_enabled():
                        search_input = input_elem
                        logger.info(f" Found visible search input for {field_label}")
                        break
               
                if not search_input:
                    # Try alternative selectors
                    alt_selectors = [
                        "div.select2-search input",
                        ".select2-dropdown input",
                        "input[class*='select2']"
                    ]
                   
                    for selector in alt_selectors:
                        try:
                            inputs = driver.find_elements(By.CSS_SELECTOR, selector)
                            for input_elem in inputs:
                                if input_elem.is_displayed() and input_elem.is_enabled():
                                    search_input = input_elem
                                    logger.info(f" Found search input using selector: {selector}")
                                    break
                            if search_input:
                                break
                        except:
                            continue
               
                if not search_input:
                    search_attempts += 1
                    logger.info(f" Search input not found, attempt {search_attempts}/{max_search_attempts}")
                    time.sleep(1)
                   
            except Exception as e:
                logger.info(f" Error finding search input: {str(e)}")
                search_attempts += 1
                time.sleep(1)
       
        if not search_input:
            logger.error(f" Could not find search input for {field_label} after {max_search_attempts} attempts")
            return False
       
        # Step 3: Clear and type the value
        logger.info(f" Typing '{value_to_select}' into search field")
        try:
            # Focus on the input
            ActionChains(driver).move_to_element(search_input).click().perform()
            time.sleep(0.5)
           
            # Clear any existing value
            search_input.clear()
            time.sleep(0.5)
           
            # Type the value character by character for better reliability
            for char in value_to_select:
                search_input.send_keys(char)
                time.sleep(0.1)  # Small delay between characters
           
            logger.info(f" Successfully typed '{value_to_select}'")
            time.sleep(2)  # Wait for search results to appear
            take_screenshot(driver, f"after_typing_{field_label.lower().replace(' ', '_')}")
           
        except Exception as e:
            logger.error(f" Error typing into search field: {str(e)}")
            return False
       
        # Step 4: Wait for and select the matching option
        logger.info(f" Looking for matching option containing '{value_to_select}'")
       
        selection_successful = False
        selection_methods = [
            # Method 1: Press Enter to select first match
            {
                "name": "Press Enter",
                "action": lambda: search_input.send_keys(Keys.ENTER)
            },
            # Method 2: Press Down Arrow then Enter
            {
                "name": "Arrow Down + Enter",
                "action": lambda: (
                    search_input.send_keys(Keys.ARROW_DOWN),
                    time.sleep(0.5),
                    search_input.send_keys(Keys.ENTER)
                )
            },
            # Method 3: Click on the first visible result
            {
                "name": "Click first result",
                "action": lambda: click_first_dropdown_result(driver, value_to_select)
            }
        ]
       
        for method in selection_methods:
            try:
                logger.info(f" Trying selection method: {method['name']}")
                method["action"]()
                time.sleep(2)  # Wait for selection to register
               
                # Verify selection was successful
                if verify_dropdown_selection(driver, field_label, value_to_select):
                    logger.info(f" Selection successful using method: {method['name']}")
                    selection_successful = True
                    break
                else:
                    logger.info(f" Method {method['name']} did not result in successful selection")
                   
            except Exception as e:
                logger.info(f" Method {method['name']} failed: {str(e)}")
                continue
       
        if selection_successful:
            take_screenshot(driver, f"after_{field_label.lower().replace(' ', '_')}_selection_success")
            return True
        else:
            logger.error(f" All selection methods failed for {field_label}")
            take_screenshot(driver, f"{field_label.lower().replace(' ', '_')}_selection_failed")
            return False
           
    except Exception as e:
        logger.error(f" Error handling Select2 dropdown for {field_label}: {str(e)}")
        take_screenshot(driver, f"{field_label.lower().replace(' ', '_')}_error")
        return False

def click_first_dropdown_result(driver, expected_value):
    """Click on the first dropdown result that matches or contains the expected value"""
    try:
        # Look for dropdown results
        result_selectors = [
            ".select2-results li",
            ".select2-results div",
            ".select2-result",
            "[class*='select2-result']"
        ]
       
        for selector in result_selectors:
            try:
                results = driver.find_elements(By.CSS_SELECTOR, selector)
                for result in results:
                    if result.is_displayed() and result.text.strip():
                        result_text = result.text.strip()
                        if expected_value.lower() in result_text.lower() or result_text.lower() in expected_value.lower():
                            logger.info(f" Found matching result: {result_text}")
                            ActionChains(driver).move_to_element(result).click().perform()
                            return True
                       
                # If no exact match, click the first visible result
                if results and results[0].is_displayed():
                    first_result_text = results[0].text.strip()
                    logger.info(f" No exact match found, clicking first result: {first_result_text}")
                    ActionChains(driver).move_to_element(results[0]).click().perform()
                    return True
                   
            except Exception as e:
                logger.debug(f" Selector {selector} failed: {str(e)}")
                continue
       
        return False
       
    except Exception as e:
        logger.error(f" Error clicking dropdown result: {str(e)}")
        return False

def verify_dropdown_selection(driver, field_label, expected_value):
    """Verify that the dropdown selection was successful"""
    try:
        # Wait a moment for the selection to register
        time.sleep(1)
       
        # Check if the dropdown now shows the selected value
        verification_methods = [
            # Method 1: Check the select2-chosen span text
            lambda: driver.execute_script(f"""
                var labels = document.querySelectorAll('label');
                for (let label of labels) {{
                    if (label.textContent.includes('{field_label}')) {{
                        let container = label.closest('.form-group').querySelector('.select2-container');
                        if (container) {{
                            let chosen = container.querySelector('.select2-chosen');
                            return chosen ? chosen.textContent.trim() : '';
                        }}
                    }}
                }}
                return '';
            """),
           
            # Method 2: Check for any element containing the expected value
            lambda: driver.execute_script(f"""
                var elements = document.querySelectorAll('.select2-chosen, .select2-selection__rendered');
                for (let elem of elements) {{
                    if (elem.textContent.includes('{expected_value}')) {{
                        return elem.textContent.trim();
                    }}
                }}
                return '';
            """)
        ]
       
        for i, method in enumerate(verification_methods):
            try:
                selected_text = method()
                if selected_text and (expected_value.lower() in selected_text.lower() or
                                    selected_text.lower() in expected_value.lower()):
                    logger.info(f" Verification method {i+1} confirmed selection: {selected_text}")
                    return True
                elif selected_text:
                    logger.info(f" Verification method {i+1} found text but no match: {selected_text}")
            except Exception as e:
                logger.debug(f" Verification method {i+1} failed: {str(e)}")
       
        # Final check: see if the dropdown is no longer showing placeholder text
        try:
            placeholder_indicators = ["Select", "Choose", "Pick", "Search"]
            current_text = driver.execute_script(f"""
                var labels = document.querySelectorAll('label');
                for (let label of labels) {{
                    if (label.textContent.includes('{field_label}')) {{
                        let container = label.closest('.form-group').querySelector('.select2-container');
                        if (container) {{
                            let chosen = container.querySelector('.select2-chosen');
                            return chosen ? chosen.textContent.trim() : '';
                        }}
                    }}
                }}
                return '';
            """)
           
            if current_text and not any(indicator.lower() in current_text.lower() for indicator in placeholder_indicators):
                logger.info(f" Selection appears successful - dropdown shows: {current_text}")
                return True
               
        except Exception as e:
            logger.debug(f" Final verification check failed: {str(e)}")
       
        logger.warning(f" Could not verify selection for {field_label}")
        return False
       
    except Exception as e:
        logger.error(f" Error verifying dropdown selection: {str(e)}")
        return False

def click_authorization_request(driver):
    """Click on the Authorization Request link after navigating to Auth & Referrals page"""
    logger.info(" Looking for Authorization Request link...")

    try:
        # Find all iframes
        frames = driver.find_elements(By.TAG_NAME, "iframe")
        logger.info(f" Found {len(frames)} iframes")
       
        # Check if we have at least 2 iframes
        if len(frames) >= 2:
            # Switch directly to iframe 2 (index 1)
            driver.switch_to.frame(frames[1])
            logger.info(" Switched to iframe 2")
           
            # Look for the Authorization Request link in iframe 2
            auth_link = WebDriverWait(driver, TIMEOUT).until(
                EC.element_to_be_clickable((By.ID, "navigation-authorizations"))
            )
           
            # Click the link
            if safe_click(driver, auth_link, "Authorization Request link in iframe 2"):
                logger.info(" Clicked on Authorization Request link in iframe 2")
                wait_for_page_load(driver)
                take_screenshot(driver, "after_auth_request_click_iframe_2")
                return True
            else:
                logger.error(" Failed to click Authorization Request link in iframe 2")
        else:
            logger.error(f" Not enough iframes found. Found only {len(frames)} iframes.")
    except Exception as e:
        logger.error(f" Error finding Authorization Request link in iframe 2: {str(e)}")
        take_screenshot(driver, "auth_request_link_not_found")
       
    # Make sure we're back to default content if there was an error
    try:
        driver.switch_to.default_content()
    except:
        pass
       
    return False

def fill_authorization_form(driver):
    """Fill out the Authorization Request form with Aetna as payer and Outpatient Authorization as request type"""
    logger.info(" Starting to fill Authorization Request form...")
    take_screenshot(driver, "before_auth_form_fill")

    # Make sure we're in the default content first
    try:
        driver.switch_to.default_content()
        logger.info(" Switched to default content")
    except Exception as e:
        logger.warning(f" Error switching to default content: {str(e)}")

    # Wait for the form to be fully loaded
    time.sleep(3)

    # Switch to iframe 2 as per screenshots
    frames = driver.find_elements(By.TAG_NAME, "iframe")
    logger.info(f" Found {len(frames)} iframes on the page")

    if len(frames) >= 2:
        try:
            driver.switch_to.frame(frames[1])  # Switch to iframe 2 (index 1)
            logger.info(" Switched to iframe 2")
            take_screenshot(driver, "iframe_2_content")
           
            # ===== PAYER SELECTION =====
            try:
                logger.info(" Looking for Payer dropdown in iframe 2...")
               
                # Using the approach from bot.py - find all select2-chosen elements
                select2_elements = driver.find_elements(By.CSS_SELECTOR, "span.select2-chosen")
                logger.info(f" Found {len(select2_elements)} select2-chosen elements")
               
                # Take screenshot to see all elements
                take_screenshot(driver, "all_select2_elements")
               
                # Find the Payer dropdown - it should be the one with "Select a Payer" text
                payer_element = None
                for i, element in enumerate(select2_elements):
                    try:
                        text = element.text
                        logger.info(f" Element {i} text: {text}")
                        if "Select a Payer" in text:
                            payer_element = element
                            logger.info(f" Found Payer dropdown with text: {text}")
                            break
                    except:
                        logger.info(f" Couldn't get text for element {i}")
               
                # If we couldn't find it by text, try using the third select2-chosen element (based on form structure)
                if not payer_element and len(select2_elements) >= 3:
                    payer_element = select2_elements[2]  # The third element (index 2) is likely the Payer dropdown
                    logger.info(" Using the third select2-chosen element as Payer dropdown")
               
                if not payer_element:
                    logger.error(" Could not find Payer dropdown")
                    take_screenshot(driver, "payer_dropdown_not_found")
                    return False
               
                # Click the Payer dropdown to open it
                safe_click(driver, payer_element, "Payer dropdown")
                logger.info(" Clicked Payer dropdown")
                time.sleep(1)
                take_screenshot(driver, "after_payer_dropdown_click")
               
                # DIRECT SELECTION: Now that the dropdown is open, directly select AETNA
                try:
                    logger.info(" Looking for AETNA option in dropdown")
                    # Try to find the AETNA option directly
                    aetna_option = WebDriverWait(driver, 5).until(
                        EC.element_to_be_clickable((By.XPATH, "//div[contains(text(), 'AETNA (COMMERCIAL & MEDICARE)')]"))
                    )
                   
                    # Click the AETNA option
                    safe_click(driver, aetna_option, "AETNA option")
                    logger.info(" Selected AETNA (COMMERCIAL & MEDICARE)")
                    time.sleep(2)  # Wait for selection to register
                    take_screenshot(driver, "after_aetna_selection")
                except Exception as e:
                    logger.error(f" Failed to select AETNA option: {str(e)}")
                   
                    # Fallback: Try using JavaScript to select AETNA
                    try:
                        logger.info(" Trying JavaScript to select AETNA")
                        driver.execute_script("""
                            var options = document.querySelectorAll('.select2-results div');
                            for (var i = 0; i < options.length; i++) {
                                if (options[i].textContent.includes('AETNA')) {
                                    options[i].click();
                                    break;
                                }
                            }
                        """)
                        logger.info(" Selected AETNA using JavaScript")
                        time.sleep(2)
                        take_screenshot(driver, "after_aetna_selection_js")
                    except Exception as e:
                        logger.error(f" JavaScript selection failed: {str(e)}")
                        return False
               
            except Exception as e:
                logger.error(f" Error selecting Payer: {str(e)}")
                take_screenshot(driver, "payer_selection_error")
                return False
           
            # ===== REQUEST TYPE SELECTION =====
            try:
                logger.info(" Looking for Request Type dropdown...")
               
                # Find all select2-chosen elements again (after selecting payer)
                select2_elements = driver.find_elements(By.CSS_SELECTOR, "span.select2-chosen")
                logger.info(f" Found {len(select2_elements)} select2-chosen elements after payer selection")
               
                # Find the Request Type dropdown - it should be the one with "Select Authorization Type" text
                request_type_element = None
                for i, element in enumerate(select2_elements):
                    try:
                        text = element.text
                        logger.info(f" Element {i} text: {text}")
                        if "Select Authorization Type" in text or "Request Type" in text:
                            request_type_element = element
                            logger.info(f" Found Request Type dropdown with text: {text}")
                            break
                    except:
                        logger.info(f" Couldn't get text for element {i}")
               
                # If we couldn't find it by text, try using the fourth select2-chosen element (based on form structure)
                if not request_type_element and len(select2_elements) >= 4:
                    request_type_element = select2_elements[3]  # The fourth element (index 3) is likely the Request Type dropdown
                    logger.info(" Using the fourth select2-chosen element as Request Type dropdown")
               
                if not request_type_element:
                    logger.error(" Could not find Request Type dropdown")
                    take_screenshot(driver, "request_type_dropdown_not_found")
                    return False
               
                # Click the Request Type dropdown
                safe_click(driver, request_type_element, "Request Type dropdown")
                logger.info(" Clicked Request Type dropdown")
                time.sleep(1)
                take_screenshot(driver, "after_request_type_dropdown_click")
               
                # DIRECT SELECTION: Now that the dropdown is open, directly select Outpatient Authorization
                try:
                    logger.info(" Looking for Outpatient Authorization option in dropdown")
                    # Try to find the Outpatient Authorization option directly
                    outpatient_option = WebDriverWait(driver, 5).until(
                        EC.element_to_be_clickable((By.XPATH, "//div[contains(text(), 'Outpatient Authorization')]"))
                    )
                   
                    # Click the Outpatient Authorization option
                    safe_click(driver, outpatient_option, "Outpatient Authorization option")
                    logger.info(" Selected Outpatient Authorization")
                    time.sleep(2)  # Wait for selection to register
                    take_screenshot(driver, "after_outpatient_selection")
                except Exception as e:
                    logger.error(f" Failed to select Outpatient Authorization option: {str(e)}")
                   
                    # Fallback: Try using JavaScript to select Outpatient Authorization
                    try:
                        logger.info(" Trying JavaScript to select Outpatient Authorization")
                        driver.execute_script("""
                            var options = document.querySelectorAll('.select2-results div');
                            for (var i = 0; i < options.length; i++) {
                                if (options[i].textContent.includes('Outpatient')) {
                                    options[i].click();
                                    break;
                                }
                            }
                        """)
                        logger.info(" Selected Outpatient Authorization using JavaScript")
                        time.sleep(2)
                        take_screenshot(driver, "after_outpatient_selection_js")
                    except Exception as e:
                        logger.error(f" JavaScript selection failed: {str(e)}")
                        return False
               
            except Exception as e:
                logger.error(f" Error selecting Request Type: {str(e)}")
                take_screenshot(driver, "request_type_selection_error")
                return False
           
            # Switch back to default content
            driver.switch_to.default_content()
            logger.info(" Switched back to default content")
            logger.info(" Successfully filled Authorization form")
            return True
           
        except Exception as e:
            logger.error(f" Error in iframe 2: {str(e)}")
            driver.switch_to.default_content()
    else:
        logger.error(f" Not enough iframes found. Found only {len(frames)} iframes.")

    return False

def handle_chrome_save_password_popup(driver):
    """Handle the Chrome save password popup by clicking 'No'"""
    logger.info(" Checking for Chrome save password popup...")
    try:
        # Try to find the "No" or "Never" button in the Chrome password save popup
        save_password_no_button = WebDriverWait(driver, 5).until(
            EC.element_to_be_clickable(LOCATORS["chrome_save_password_no"])
        )
       
        # Click the "No" button
        safe_click(driver, save_password_no_button, "Chrome save password 'No' button")
        logger.info(" Clicked 'No' on Chrome save password popup")
        time.sleep(1)
        take_screenshot(driver, "after_chrome_popup_no_click")
        return True
    except Exception as e:
        logger.info(f" Chrome save password popup not found or already handled: {str(e)}")
        return False

def fill_place_of_service(driver):
    """Fill out the Place of Service field with '11 - Office' and select the option"""
    logger.info(" Starting to fill Place of Service field...")
    take_screenshot(driver, "before_place_of_service_fill")
   
    # Using the handle_select2_field function from aetnapriorauthcopy1.py
    return handle_select2_field(driver, "Place of Service", PLACE_OF_SERVICE)

def fill_patient_info_form(driver, patient_data):
    """Fill out the Patient Information form with Member ID, DOB, and Provider"""
    member_id = patient_data.get("Member ID", "")
    dob = patient_data.get("Patient Date of Birth", "")
    provider_name = PROVIDER_NAME  # Use hardcoded provider name

    logger.info(f" Starting to fill Patient Information form with Member ID: {member_id}, DOB: {dob}...")
    take_screenshot(driver, "before_patient_info_fill")

    # Make sure we're in the default content first
    try:
        driver.switch_to.default_content()
        logger.info(" Switched to default content")
    except Exception as e:
        logger.warning(f" Error switching to default content: {str(e)}")

    # Wait for the form to be fully loaded
    time.sleep(3)

    # Switch to iframe 2 as per instructions
    frames = driver.find_elements(By.TAG_NAME, "iframe")
    logger.info(f" Found {len(frames)} iframes on the page")

    if len(frames) >= 2:
        try:
            driver.switch_to.frame(frames[1])  # Switch to iframe 2 (index 1)
            logger.info(" Switched to iframe 2")
            take_screenshot(driver, "iframe_2_content")
           
            # ===== MEMBER ID FIELD =====
            try:
                logger.info(" Looking for Member ID field...")
               
                # Try to find the Member ID input field
                member_id_field = None
               
                # Try different approaches to find the Member ID field
                try:
                    member_id_field = driver.find_element(By.CSS_SELECTOR, "input#subscriber\\.memberId")
                    logger.info(" Found Member ID field by ID")
                except:
                    logger.info(" Could not find Member ID field by ID, trying other methods")
               
                if not member_id_field:
                    try:
                        member_id_field = driver.find_element(By.XPATH, "//input[contains(@aria-labelledby, 'subscriber.memberId')]")
                        logger.info(" Found Member ID field by aria-labelledby")
                    except:
                        logger.info(" Could not find Member ID field by aria-labelledby")
               
                # If still not found, use JavaScript to find by nearby text or attributes
                if not member_id_field:
                    logger.info(" Using JavaScript to find Member ID field")
                    member_id_field = driver.execute_script("""
                        // Find all input fields
                        var inputs = document.querySelectorAll('input[type="text"]');
                       
                        // Look for the input field that's near a label with "Member ID"
                        for (let input of inputs) {
                            // Check if this input has an error message about Member ID
                            let parentDiv = input.closest('div');
                            if (parentDiv) {
                                let errorMsg = parentDiv.querySelector('.invalid-feedback');
                                if (errorMsg && errorMsg.textContent.includes('Member ID')) {
                                    return input;
                                }
                            }
                           
                            // Check if this input is near a label with "Member ID"
                            let nearbyText = input.parentElement.textContent || '';
                            if (nearbyText.includes('Member ID')) {
                                return input;
                            }
                           
                            // Check attributes
                            if (input.id.includes('memberId') ||
                                input.name.includes('memberId') ||
                                input.getAttribute('aria-label')?.includes('Member ID')) {
                                return input;
                            }
                        }
                       
                        return null;
                    """)
               
                if member_id_field:
                    logger.info(" Found Member ID field")
                   
                    # Highlight the field to confirm in screenshots
                    driver.execute_script("arguments[0].style.border='5px solid green'", member_id_field)
                    time.sleep(1)
                   
                    # Clear field first
                    member_id_field.clear()
                    time.sleep(0.5)
                   
                    # Click to focus
                    ActionChains(driver).move_to_element(member_id_field).click().perform()
                    time.sleep(0.5)
                   
                    # Send keys with delay
                    for char in member_id:
                        member_id_field.send_keys(char)
                        time.sleep(0.1)
                   
                    logger.info(f" Filled Member ID: {member_id}")
                    take_screenshot(driver, "member_id_filled")
                else:
                    logger.error(" Could not find Member ID field")
                    return False
            except Exception as e:
                logger.error(f" Error filling Member ID: {str(e)}")
                take_screenshot(driver, "member_id_error")
                return False
           
            # ===== PATIENT DATE OF BIRTH FIELD =====
            try:
                logger.info(" Looking for Patient Date of Birth field...")
               
                # Try to find the DOB input field
                dob_field = None
               
                # Try different approaches to find the DOB field
                try:
                    dob_field = driver.find_element(By.CSS_SELECTOR, "input#patient\\.birthDate")
                    logger.info(" Found Patient DOB field by ID")
                except:
                    logger.info(" Could not find Patient DOB field by ID, trying other methods")
               
                if not dob_field:
                    try:
                        dob_field = driver.find_element(By.XPATH, "//input[contains(@placeholder, 'mm/dd/yyyy')]")
                        logger.info(" Found Patient DOB field by placeholder")
                    except:
                        logger.info(" Could not find Patient DOB field by placeholder")
               
                # If still not found, use JavaScript to find by nearby text or attributes
                if not dob_field:
                    logger.info(" Using JavaScript to find Patient DOB field")
                    dob_field = driver.execute_script("""
                        // Find all input fields
                        var inputs = document.querySelectorAll('input[type="text"]');
                       
                        // Look for the input field that's near a label with "Date of Birth" or "DOB"
                        for (let input of inputs) {
                            // Check if this input has a date format placeholder
                            if (input.placeholder && input.placeholder.includes('mm/dd/yyyy')) {
                                return input;
                            }
                           
                            // Check if this input is near a label with "Date of Birth" or "DOB"
                            let nearbyText = input.parentElement.textContent || '';
                            if (nearbyText.includes('Date of Birth') || nearbyText.includes('DOB')) {
                                return input;
                            }
                           
                            // Check attributes
                            if (input.id.includes('birthDate') ||
                                input.name.includes('birthDate') ||
                                input.getAttribute('aria-label')?.includes('Date of Birth')) {
                                return input;
                            }
                        }
                       
                        return null;
                    """)
               
                if dob_field:
                    logger.info(" Found Patient DOB field")
                   
                    # Highlight the field to confirm in screenshots
                    driver.execute_script("arguments[0].style.border='5px solid green'", dob_field)
                    time.sleep(1)
                   
                    # Clear field first
                    dob_field.clear()
                    time.sleep(0.5)
                   
                    # Click to focus
                    ActionChains(driver).move_to_element(dob_field).click().perform()
                    time.sleep(0.5)
                   
                    # Send keys with delay
                    for char in dob:
                        dob_field.send_keys(char)
                        time.sleep(0.1)
                   
                    # Press Tab to trigger any validation
                    dob_field.send_keys(Keys.TAB)
                   
                    logger.info(f" Filled Patient DOB: {dob}")
                    take_screenshot(driver, "dob_filled")
                else:
                    logger.error(" Could not find Patient DOB field")
                    return False
            except Exception as e:
                logger.error(f" Error filling Patient DOB: {str(e)}")
                take_screenshot(driver, "dob_error")
                return False
           
            # ===== PROVIDER SELECTION =====
            try:
                logger.info(" Looking for Provider dropdown...")
               
                # Try to find the Provider dropdown
                provider_dropdown_elements = driver.find_elements(By.CSS_SELECTOR, "span.select2-chosen")
                logger.info(f" Found {len(provider_dropdown_elements)} select2-chosen elements")
               
                # Find the Provider dropdown - it should be the one with "Select Provider" text
                provider_dropdown = None
                for i, element in enumerate(provider_dropdown_elements):
                    try:
                        text = element.text
                        logger.info(f" Element {i} text: {text}")
                        if "Select Provider" in text or "Provider" in text:
                            provider_dropdown = element
                            logger.info(f" Found Provider dropdown with text: {text}")
                            break
                    except:
                        logger.info(f" Couldn't get text for element {i}")
               
                # If we couldn't find it by text, try using JavaScript
                if not provider_dropdown:
                    logger.info(" Trying JavaScript to find Provider dropdown")
                    provider_dropdown = driver.execute_script("""
                        // Find all span elements with select2-chosen class
                        var spans = document.querySelectorAll('span.select2-chosen');
                       
                        // Look for the span that's near a label with "Provider"
                        for (let span of spans) {
                            let nearbyText = span.parentElement.parentElement.textContent || '';
                            if (nearbyText.includes('Provider')) {
                                return span;
                            }
                        }
                       
                        return null;
                    """)
               
                if provider_dropdown:
                    logger.info(" Found Provider dropdown")
                   
                    # Click the Provider dropdown to open it
                    safe_click(driver, provider_dropdown, "Provider dropdown")
                    logger.info(" Clicked Provider dropdown")
                    time.sleep(2)  # Wait longer for dropdown to fully open
                    take_screenshot(driver, "after_provider_dropdown_click")
                   
                    # Instead of typing, directly select one of the available options (KOLLIPARA or MOMOH)
                    try:
                        # First try to find KOLLIPARA option
                        logger.info(" Looking for KOLLIPARA provider option")
                        kollipara_option = None
                       
                        # Try different methods to find the KOLLIPARA option
                        try:
                            kollipara_option = WebDriverWait(driver, 5).until(
                                EC.element_to_be_clickable((By.XPATH, "//div[contains(text(), 'KOLLIPARA')]"))
                            )
                            logger.info(" Found KOLLIPARA option by text")
                        except:
                            logger.info(" Could not find KOLLIPARA option by text, trying other methods")
                       
                        # If not found, try using JavaScript to find the option
                        if not kollipara_option:
                            logger.info(" Using JavaScript to find KOLLIPARA option")
                            kollipara_option = driver.execute_script("""
                                // Find all dropdown options
                                var options = document.querySelectorAll('.select2-results div');
                               
                                // Look for the option containing KOLLIPARA
                                for (let option of options) {
                                    if (option.textContent.includes('KOLLIPARA')) {
                                        return option;
                                    }
                                }
                               
                                return null;
                            """)
                       
                        # If KOLLIPARA option is found, click it
                        if kollipara_option:
                            logger.info(" Found KOLLIPARA option, clicking it")
                            safe_click(driver, kollipara_option, "KOLLIPARA option")
                            logger.info(" Selected KOLLIPARA provider")
                            time.sleep(2)  # Wait for selection to register
                            take_screenshot(driver, "after_provider_selection")
                        else:
                            # If KOLLIPARA not found, try to find MOMOH option
                            logger.info(" KOLLIPARA option not found, looking for MOMOH option")
                            momoh_option = None
                           
                            try:
                                momoh_option = WebDriverWait(driver, 5).until(
                                    EC.element_to_be_clickable((By.XPATH, "//div[contains(text(), 'MOMOH')]"))
                                )
                                logger.info(" Found MOMOH option by text")
                            except:
                                logger.info(" Could not find MOMOH option by text, trying other methods")
                           
                            # If not found, try using JavaScript to find the option
                            if not momoh_option:
                                logger.info(" Using JavaScript to find MOMOH option")
                                momoh_option = driver.execute_script("""
                                    // Find all dropdown options
                                    var options = document.querySelectorAll('.select2-results div');
                                   
                                    // Look for the option containing MOMOH
                                    for (let option of options) {
                                        if (option.textContent.includes('MOMOH')) {
                                            return option;
                                        }
                                    }
                                   
                                    return null;
                                """)
                           
                            # If MOMOH option is found, click it
                            if momoh_option:
                                logger.info(" Found MOMOH option, clicking it")
                                safe_click(driver, momoh_option, "MOMOH option")
                                logger.info(" Selected MOMOH provider")
                                time.sleep(2)  # Wait for selection to register
                                take_screenshot(driver, "after_provider_selection")
                            else:
                                # If neither option is found, try selecting the first option in the dropdown
                                logger.info(" Neither KOLLIPARA nor MOMOH found, trying to select first available option")
                                first_option = driver.execute_script("""
                                    // Find all dropdown options
                                    var options = document.querySelectorAll('.select2-results div');
                                   
                                    // Return the first option if available
                                    return options.length > 0 ? options[0] : null;
                                """)
                               
                                if first_option:
                                    logger.info(" Found first available option, clicking it")
                                    safe_click(driver, first_option, "First available option")
                                    logger.info(" Selected first available provider")
                                    time.sleep(2)  # Wait for selection to register
                                    take_screenshot(driver, "after_provider_selection")
                                else:
                                    # Last resort: try using keyboard navigation
                                    logger.info(" No options found, trying keyboard navigation")
                                    ActionChains(driver).send_keys(Keys.ARROW_DOWN).perform()
                                    time.sleep(1)
                                    ActionChains(driver).send_keys(Keys.ENTER).perform()
                                    time.sleep(2)
                                    logger.info(" Used keyboard navigation to select provider")
                                    take_screenshot(driver, "after_provider_selection_keyboard")
                    except Exception as e:
                        logger.error(f" Error selecting provider option: {str(e)}")
                        take_screenshot(driver, "provider_option_selection_error")
                        return False
                   
                    # FIRST NEXT BUTTON: Click the Next button after provider selection
                    # Stay in iframe 2 to find the Next button
                    try:
                        # We're already in iframe 2, so look for the Next button here
                        logger.info(" Looking for first Next button in iframe 2...")
                       
                        # First try to find the button with id="authWizardNextButton"
                        try:
                            next_button = WebDriverWait(driver, TIMEOUT).until(
                                EC.element_to_be_clickable((By.ID, "authWizardNextButton"))
                            )
                            logger.info(" Found Next button by ID: authWizardNextButton")
                        except:
                            logger.info(" Could not find Next button by ID, trying other methods")
                           
                            # Try to find by class and text
                            try:
                                next_button = WebDriverWait(driver, TIMEOUT).until(
                                    EC.element_to_be_clickable((By.XPATH, "//button[contains(@class, 'btn') and contains(text(), 'Next')]"))
                                )
                                logger.info(" Found Next button by class and text")
                            except:
                                logger.info(" Could not find Next button by class and text, trying more generic selector")
                               
                                # Try more generic selector
                                next_button = WebDriverWait(driver, TIMEOUT).until(
                                    EC.element_to_be_clickable((By.XPATH, "//button[contains(text(), 'Next')]"))
                                )
                                logger.info(" Found Next button by text")
                       
                        # Highlight the Next button in screenshots
                        driver.execute_script("arguments[0].style.border='5px solid blue'", next_button)
                        time.sleep(1)
                        take_screenshot(driver, "next_button_highlighted_in_iframe2")
                       
                        # Click the Next button
                        safe_click(driver, next_button, "Next button in iframe 2 (after provider selection)")
                        logger.info(" Clicked Next button in iframe 2 after provider selection")
                       
                        # Wait for the next page to load
                        time.sleep(3)
                        take_screenshot(driver, "after_first_next_button_click")
                       
                        # Handle Chrome save password popup that might appear after clicking Next
                        handle_chrome_save_password_popup(driver)
                       
                        # Now handle the Place of Service field
                        logger.info(" Now filling Place of Service field...")
                        if fill_place_of_service(driver):
                            logger.info(" Successfully filled Place of Service field")
                           
                            return True
                        else:
                            logger.error(" Failed to fill Place of Service field")
                            return False
                    except Exception as e:
                        logger.error(f" Failed to click first Next button: {str(e)}")
                        take_screenshot(driver, "first_next_button_failed")
                        return False
                else:
                    logger.error(" Could not find Provider dropdown")
                    return False
            except Exception as e:
                logger.error(f" Error selecting Provider: {str(e)}")
                take_screenshot(driver, "provider_selection_error")
                return False
           
            # Switch back to default content after completing the form
            driver.switch_to.default_content()
            logger.info(" Switched back to default content after filling form")
           
            logger.info(" Successfully filled Patient Information form")
            return True
           
        except Exception as e:
            logger.error(f" Error in iframe 2: {str(e)}")
            driver.switch_to.default_content()
    else:
        logger.error(f" Not enough iframes found. Found only {len(frames)} iframes.")

    return False

def fill_diagnosis_procedure_form(driver, patient_data):
    """Fill out the Diagnosis and Procedure form with improved dropdown handling"""
    diagnosis_code = patient_data.get("Diagnosis Code", "")
    logging.info(f" Diagnosis Code received: {diagnosis_code}")
    procedure_code = patient_data.get("Procedure Code", "")
    logging.info(f" Procedure Code received: {procedure_code}")
    
    # Get from_date and format it properly to MM/DD/YYYY
    raw_from_date = patient_data.get("from_date") or "2025-07-17"
    from_date = format_date_for_form(raw_from_date)  # This will convert to MM/DD/YYYY format
    
    procedure_quantity = "1"  # Hardcoded as per original script
    procedure_quantity_type = "Days"  # Hardcoded as per original script
    logger.info(f" Starting to fill Diagnosis and Procedure form with Diagnosis: {diagnosis_code}, Procedure: {procedure_code}, From Date: {from_date}...")
    take_screenshot(driver, "before_diagnosis_procedure_form")
   
    # Make sure we're in the correct iframe
    try:
        driver.switch_to.default_content()
        frames = driver.find_elements(By.TAG_NAME, "iframe")
        if len(frames) >= 2:
            driver.switch_to.frame(frames[1])
            logger.info(" Switched to iframe 2")
        else:
            logger.error(f" Not enough iframes found. Found only {len(frames)} iframes.")
            return False
    except Exception as e:
        logger.error(f" Error switching to iframe: {str(e)}")
        return False
   
    # Wait for the form to load
    time.sleep(3)
    take_screenshot(driver, "diagnosis_procedure_form_loaded")
   
    # Track field completion status
    diagnosis_filled = False
    procedure_filled = False
    from_date_filled = False
    quantity_filled = False
    days_selected = False
   
    # ===== IMPROVED DIAGNOSIS CODE FIELD =====
    try:
        logger.info(" Looking for Diagnosis Code dropdown...")
       
        if handle_select2_dropdown_with_selection(driver, "Diagnosis Code", diagnosis_code):
            diagnosis_filled = True
            logger.info(f" Successfully filled and selected Diagnosis Code: {diagnosis_code}")
        else:
            logger.error(" Failed to fill Diagnosis Code")
           
    except Exception as e:
        logger.error(f" Error filling Diagnosis Code: {str(e)}")
        take_screenshot(driver, "diagnosis_code_error")
   
    # ===== IMPROVED PROCEDURE CODE FIELD =====
    try:
        logger.info(" Looking for Procedure Code dropdown...")
       
        if handle_select2_dropdown_with_selection(driver, "Procedure Code", procedure_code):
            procedure_filled = True
            logger.info(f" Successfully filled and selected Procedure Code: {procedure_code}")
        else:
            logger.error(" Failed to fill Procedure Code")
           
    except Exception as e:
        logger.error(f" Error filling Procedure Code: {str(e)}")
        take_screenshot(driver, "procedure_code_error")
   
    # ===== FROM DATE FIELD =====
    try:
        logger.info(f" Looking for From Date field to fill with formatted date: {from_date}")
        
        # Step 1: Find and click the calendar button to activate the date picker
        logger.info(" Step 1: Looking for calendar button to activate date picker")
        
        calendar_button = None
        
        # Method 1: Find by the ng-click attribute seen in screenshot
        try:
            calendar_button = driver.find_element(
                By.CSS_SELECTOR, 
                "button[ng-click='dfc.dateFocus()']"
            )
            logger.info(" Found calendar button by ng-click attribute")
        except:
            logger.info(" Could not find calendar button by ng-click attribute")
        
        # Method 2: Find by icon class or calendar-related attributes
        if not calendar_button:
            try:
                calendar_button = driver.find_element(
                    By.CSS_SELECTOR,
                    "button[aria-label='Calendar'], button.btn-default[type='button']"
                )
                logger.info(" Found calendar button by aria-label or class")
            except:
                logger.info(" Could not find calendar button by aria-label or class")
        
        # Method 3: Find calendar button near the from_date field
        if not calendar_button:
            try:
                # Find the from_date input field first
                from_date_field = driver.find_element(
                    By.CSS_SELECTOR,
                    "input[id*='fromDate'], input[placeholder*='__/__/____']"
                )
                
                # Look for a button in the same parent container
                parent_container = from_date_field.find_element(By.XPATH, "./..")
                calendar_button = parent_container.find_element(
                    By.CSS_SELECTOR,
                    "button"
                )
                logger.info(" Found calendar button near from_date field")
            except:
                logger.info(" Could not find calendar button near from_date field")
        
        # Method 4: JavaScript approach to find the button
        if not calendar_button:
            logger.info(" Using JavaScript to find calendar button")
            calendar_button = driver.execute_script("""
                // Look for button with calendar-related attributes
                var buttons = document.querySelectorAll('button');
                for (var i = 0; i < buttons.length; i++) {
                    var btn = buttons[i];
                    
                    // Check for ng-click with dateFocus
                    if (btn.getAttribute('ng-click') && 
                        btn.getAttribute('ng-click').includes('dateFocus')) {
                        return btn;
                    }
                    
                    // Check for calendar icon or aria-label
                    if (btn.getAttribute('aria-label') === 'Calendar' ||
                        btn.className.includes('calendar') ||
                        btn.innerHTML.includes('calendar')) {
                        return btn;
                    }
                    
                    // Check if button is next to a date input
                    var nextSibling = btn.previousElementSibling;
                    if (nextSibling && nextSibling.tagName === 'INPUT' &&
                        (nextSibling.placeholder.includes('__/__/____') ||
                         nextSibling.id.includes('fromDate'))) {
                        return btn;
                    }
                }
                return null;
            """)
        
        if calendar_button:
            logger.info(" Found calendar button, clicking to activate date picker")
            
            # Scroll to the button and click it
            driver.execute_script("arguments[0].scrollIntoView({block: 'center'});", calendar_button)
            time.sleep(1)
            
            # Click the calendar button using safe_click
            safe_click(driver, calendar_button, "Calendar button")
            logger.info(" Clicked calendar button successfully")
            
            # Wait for the date picker to activate
            time.sleep(2)
            take_screenshot(driver, "after_calendar_button_click")
            
        else:
            logger.warning(" Could not find calendar button, proceeding without clicking it")
        
        # Step 2: Now find and fill the from_date field
        logger.info(" Step 2: Looking for from_date field after calendar activation")
        
        from_date_field = None
        
        # Try multiple approaches to find the field
        field_selectors = [
            "input[id*='fromDate']",
            "input[id*='FromDate']", 
            "input[name*='fromDate']",
            "input[placeholder*='__/__/____']",
            "input.hasDatepicker",
            "input[ng-model*='fromDate']"
        ]
        
        for selector in field_selectors:
            try:
                from_date_field = driver.find_element(By.CSS_SELECTOR, selector)
                if from_date_field.is_displayed() and from_date_field.is_enabled():
                    logger.info(f" Found from_date field using selector: {selector}")
                    break
            except:
                continue
        
        # JavaScript fallback to find the field
        if not from_date_field:
            logger.info(" Using JavaScript to find from_date field")
            from_date_field = driver.execute_script("""
                // Look for input fields that could be the from_date field
                var inputs = document.querySelectorAll('input[type="text"]');
                
                for (var i = 0; i < inputs.length; i++) {
                    var input = inputs[i];
                    
                    // Check ID and name attributes
                    if ((input.id && input.id.toLowerCase().includes('fromdate')) ||
                        (input.name && input.name.toLowerCase().includes('fromdate'))) {
                        return input;
                    }
                    
                    // Check placeholder
                    if (input.placeholder && input.placeholder.includes('__/__/____')) {
                        return input;
                    }
                    
                    // Check if it's near a label with "From Date"
                    var labels = document.querySelectorAll('label');
                    for (var j = 0; j < labels.length; j++) {
                        if (labels[j].textContent.includes('From Date')) {
                            var container = labels[j].closest('.form-group');
                            if (container && container.contains(input)) {
                                return input;
                            }
                        }
                    }
                }
                
                return null;
            """)
        
        if from_date_field:
            logger.info(" Found from_date field, proceeding to fill it")
            
            # Step 3: Clear and fill the field with properly formatted date
            logger.info(f" Step 3: Filling from_date field with formatted date: {from_date}")
            
            # Make sure the field is editable
            driver.execute_script("""
                arguments[0].readOnly = false;
                arguments[0].removeAttribute('readonly');
                arguments[0].removeAttribute('disabled');
            """, from_date_field)
            
            # Focus on the field
            ActionChains(driver).move_to_element(from_date_field).click().perform()
            time.sleep(0.5)
            
            # Clear the field completely
            from_date_field.clear()
            time.sleep(0.5)
            
            # Use Ctrl+A and Delete to ensure field is completely clear
            from_date_field.send_keys(Keys.CONTROL + "a")
            time.sleep(0.2)
            from_date_field.send_keys(Keys.DELETE)
            time.sleep(0.5)
            
            # Send the formatted date value character by character
            logger.info(f" Typing formatted date: {from_date}")
            for char in from_date:
                from_date_field.send_keys(char)
                time.sleep(0.15)  # Slightly slower typing for date fields
            
            # Trigger events to ensure the value is registered
            driver.execute_script("""
                var field = arguments[0];
                var value = arguments[1];
                
                // Set the value directly
                field.value = value;
                
                // Trigger all necessary events
                field.dispatchEvent(new Event('input', { bubbles: true }));
                field.dispatchEvent(new Event('change', { bubbles: true }));
                field.dispatchEvent(new Event('keyup', { bubbles: true }));
                field.dispatchEvent(new Event('blur', { bubbles: true }));
                
                // Trigger Angular-specific events if needed
                if (window.angular) {
                    var scope = angular.element(field).scope();
                    if (scope) {
                        scope.$apply();
                    }
                }
                
                // Trigger any custom date validation events
                if (field.onchange) field.onchange();
                if (field.oninput) field.oninput();
            """, from_date_field, from_date)
            
            # Press Tab to move to next field and trigger validation
            from_date_field.send_keys(Keys.TAB)
            time.sleep(1)
            
            logger.info(f" Successfully filled from_date field with formatted date: {from_date}")
            take_screenshot(driver, "from_date_filled_successfully")
            
            # Verify the value was set correctly
            field_value = from_date_field.get_attribute('value')
            logger.info(f" Field value after input: '{field_value}'")
            
            if field_value and field_value != '__/__/____' and field_value.strip() != '':
                logger.info(f" ✓ Verified from_date field value: {field_value}")
                from_date_filled = True
            else:
                logger.warning(f" ⚠ from_date field value verification failed. Expected: {from_date}, Got: {field_value}")
                # Try one more time with direct JavaScript setting
                logger.info(" Attempting direct JavaScript value setting as fallback")
                driver.execute_script("""
                    var field = arguments[0];
                    var value = arguments[1];
                    field.focus();
                    field.value = value;
                    field.dispatchEvent(new Event('input', { bubbles: true }));
                    field.dispatchEvent(new Event('change', { bubbles: true }));
                """, from_date_field, from_date)
                time.sleep(1)
                
                # Check again
                field_value_retry = from_date_field.get_attribute('value')
                if field_value_retry and field_value_retry != '__/__/____':
                    logger.info(f" ✓ Fallback method successful: {field_value_retry}")
                    from_date_filled = True
                else:
                    logger.error(f" ✗ All methods failed to set from_date field")
                    from_date_filled = False
                
        else:
            logger.error(" Could not find from_date field after calendar activation")
            from_date_filled = False
            
    except Exception as e:
        logger.error(f" Error filling From Date: {str(e)}")
        take_screenshot(driver, "from_date_error")
        from_date_filled = False
   
    # ===== PROCEDURE SERVICE QUANTITY FIELD =====
    try:
        logger.info(" Looking for Procedure Service Quantity field...")
       
        # Try to find the Quantity field
        quantity_field = None
       
        try:
            quantity_field = driver.find_element(By.CSS_SELECTOR, "input[id*='serviceQuantity']")
            logger.info(" Found Quantity field by ID pattern")
        except:
            logger.info(" Could not find Quantity field by ID pattern")
       
        if not quantity_field:
            # Try by XPath with label
            try:
                quantity_field = driver.find_element(By.XPATH, "//label[contains(text(), 'Procedure Service Quantity')]/following::input[1]")
                logger.info(" Found Quantity field by label XPath")
            except:
                logger.info(" Could not find Quantity field by label XPath")
       
        if not quantity_field:
            # Try JavaScript to find it
            logger.info(" Using JavaScript to find Procedure Service Quantity field")
            quantity_field = driver.execute_script("""
                // Try to find by label text
                var labels = document.querySelectorAll('label');
                for (let label of labels) {
                    if (label.textContent.includes('Quantity') || label.textContent.includes('Service Quantity')) {
                        // Find the closest input field
                        let input = label.closest('.form-group').querySelector('input[type="text"]');
                        if (input) return input;
                    }
                }
               
                // Try to find by ID pattern
                var inputs = document.querySelectorAll('input[type="text"]');
                for (let input of inputs) {
                    if (input.id && (input.id.includes('quantity') || input.id.includes('Quantity'))) {
                        return input;
                    }
                }
               
                return null;
            """)
       
        if quantity_field:
            # Clear and fill the field
            quantity_field.clear()
            quantity_field.send_keys(procedure_quantity)
            # Press Tab to move to next field
            quantity_field.send_keys(Keys.TAB)
            logger.info(f" Entered Procedure Quantity: {procedure_quantity}")
            time.sleep(1)
            take_screenshot(driver, "after_quantity_entry")
            quantity_filled = True
        else:
            logger.error(" Could not find Procedure Quantity field")
    except Exception as e:
        logger.error(f" Error filling Procedure Quantity: {str(e)}")
        take_screenshot(driver, "quantity_error")
   
    # ===== PROCEDURE SERVICE QUANTITY TYPE FIELD =====
    try:
        logger.info(" Looking for Procedure Service Quantity Type dropdown...")
       
        # Try to find the Quantity Type dropdown
        quantity_type_dropdown = None
       
        # First try to find by the dropdown container with a specific class
        try:
            quantity_type_dropdown = driver.find_element(By.CSS_SELECTOR, "div.select2-container[id*='quantityType']")
            logger.info(" Found Quantity Type dropdown by container ID")
        except:
            logger.info(" Could not find Quantity Type dropdown by container ID")
       
        # If not found, try to find by the dropdown arrow
        if not quantity_type_dropdown:
            try:
                quantity_type_dropdown = driver.find_element(By.XPATH, "//label[contains(text(), 'Quantity Type')]/following::div[contains(@class, 'select2-container')][1]")
                logger.info(" Found Quantity Type dropdown by label and container")
            except:
                logger.info(" Could not find Quantity Type dropdown by label and container")
       
        # If still not found, try using JavaScript
        if not quantity_type_dropdown:
            logger.info(" Using JavaScript to find Quantity Type dropdown")
            quantity_type_dropdown = driver.execute_script("""
                // Try to find by label text
                var labels = document.querySelectorAll('label');
                for (let label of labels) {
                    if (label.textContent.includes('Quantity Type')) {
                        // Find the closest select2 container
                        let container = label.closest('.form-group').querySelector('.select2-container');
                        if (container) return container;
                    }
                }
               
                // Try to find by ID pattern
                var containers = document.querySelectorAll('.select2-container');
                for (let container of containers) {
                    if (container.id && container.id.includes('quantityType')) {
                        return container;
                    }
                }
               
                return null;
            """)
       
        if quantity_type_dropdown:
            # Click to open dropdown
            logger.info(" Attempting to click Quantity Type dropdown...")
           
            # Try multiple approaches to click the dropdown
            click_success = False
           
            # Approach 3: JavaScript click (most reliable based on logs)
            try:
                for attempt in range(3):  # Try up to 3 times
                    try:
                        driver.execute_script("arguments[0].click();", quantity_type_dropdown)
                        click_success = True
                        logger.info(f" JavaScript click on Quantity Type dropdown successful (attempt {attempt+1})")
                        break
                    except Exception as e:
                        logger.info(f" JavaScript click attempt {attempt+1} failed: {str(e)}")
                        time.sleep(0.5)
            except Exception as e:
                logger.info(f" All JavaScript click attempts failed: {str(e)}")
           
            if click_success:
                logger.info(" Clicked Quantity Type dropdown")
                time.sleep(2)
                take_screenshot(driver, "after_quantity_type_dropdown_click")
               
                # Approach 2: Keyboard navigation (most reliable based on logs)
                try:
                    logger.info(" Trying keyboard navigation to select Days")
                    # Press Down arrow to highlight first option, then Enter to select it
                    ActionChains(driver).send_keys(Keys.DOWN).perform()
                    time.sleep(0.5)
                    ActionChains(driver).send_keys(Keys.ENTER).perform()
                    logger.info(" Used keyboard navigation to select first option (should be Days)")
                    days_selected = True
                    time.sleep(1)
                    take_screenshot(driver, "after_quantity_type_selection_keyboard")
                except Exception as e:
                    logger.info(f" Keyboard navigation failed: {str(e)}")
               
                # Check if Days is already selected (it might be pre-selected)
                if not days_selected:
                    try:
                        # Check if the dropdown already shows "Days"
                        days_text = driver.execute_script("""
                            var container = document.querySelector('div[id*="quantityType"]');
                            return container ? container.textContent.trim() : '';
                        """)
                       
                        if days_text and "Days" in days_text:
                            logger.info(" Days is already selected in Quantity Type dropdown")
                            days_selected = True
                        else:
                            # Try one more approach - direct JavaScript selection
                            logger.info(" Trying direct JavaScript selection for Days")
                            days_selected = driver.execute_script("""
                                // Try to find the select element
                                var select = document.querySelector('select[id*="quantityType"]');
                                if (select) {
                                    // Find the Days option
                                    for (var i = 0; i < select.options.length; i++) {
                                        if (select.options[i].text === 'Days') {
                                            select.selectedIndex = i;
                                            select.dispatchEvent(new Event('change', { bubbles: true }));
                                            return true;
                                        }
                                    }
                                }
                               
                                // If select element not found, try to find the select2 container
                                var container = document.querySelector('div[id*="quantityType"]');
                                if (container && container.textContent.includes('Days')) {
                                    return true;  // Days is already selected
                                }
                               
                                return false;
                            """)
                           
                            if days_selected:
                                logger.info(" Selected Days using direct JavaScript")
                            else:
                                logger.warning(" Could not select Days using any method")
                    except Exception as e:
                        logger.error(f" Error checking if Days is already selected: {str(e)}")
            else:
                logger.error(" Failed to click Quantity Type dropdown after multiple attempts")
               
                # Check if Days is already selected without clicking the dropdown
                try:
                    days_text = driver.execute_script("""
                        var container = document.querySelector('div[id*="quantityType"]');
                        return container ? container.textContent.trim() : '';
                    """)
                   
                    if days_text and "Days" in days_text:
                        logger.info(" Days is already selected in Quantity Type dropdown")
                        days_selected = True
                    else:
                        logger.warning(" Could not verify Days selection")
                except Exception as e: 
                    logger.error(f" Error checking Days selection: {str(e)}")
        else:
            # Try to find regular select element
            try:
                quantity_type_select = driver.find_element(By.CSS_SELECTOR, "select[id*='quantityType']")
                logger.info(" Found Quantity Type select by ID pattern")
               
                # Use Select class to select option
                from selenium.webdriver.support.ui import Select
                select = Select(quantity_type_select)
                select.select_by_visible_text(procedure_quantity_type)
                logger.info(f" Selected Quantity Type: {procedure_quantity_type}")
                days_selected = True
                time.sleep(1)
                take_screenshot(driver, "after_quantity_type_selection_select")
            except Exception as e:
                logger.error(f" Could not find or interact with Quantity Type dropdown: {str(e)}")
                days_selected = False
    except Exception as e:
        logger.error(f" Error selecting Quantity Type: {str(e)}")
        take_screenshot(driver, "quantity_type_error")
        days_selected = False
   
    # ===== VERIFY ALL FIELDS ARE FILLED BEFORE CLICKING NEXT =====
    fields_status = {
        "Diagnosis Code": diagnosis_filled,
        "Procedure Code": procedure_filled,
        "From Date": from_date_filled,
        "Procedure Quantity": quantity_filled,
        "Quantity Type (Days)": days_selected
    }
   
    logger.info(" Field completion status:")
    for field, status in fields_status.items():
        logger.info(f" - {field}: {'Completed' if status else 'Not completed'}")
   
    # Check if all required fields are filled
    all_fields_filled = diagnosis_filled and procedure_filled and from_date_filled and days_selected
   
    # Check the UI state to see if fields appear to be filled
    if not all_fields_filled:
        ui_state = driver.execute_script("""
            var state = {
                diagnosisFilled: false,
                procedureFilled: false,
                fromDateFilled: false,
                quantityFilled: false,
                daysSelected: false
            };
           
            // Check Diagnosis Code
            var diagnosisContainer = document.querySelector('div[id*="diagnosisCode"]');
            if (diagnosisContainer && diagnosisContainer.textContent.trim() !== '') {
                state.diagnosisFilled = true;
            }
           
            // Check Procedure Code
            var procedureContainer = document.querySelector('div[id*="procedureCode"]');
            if (procedureContainer && procedureContainer.textContent.trim() !== '') {
                state.procedureFilled = true;
            }
           
            // Check From Date
            var fromDateField = document.querySelector('input[id*="fromDate"]');
            if (fromDateField && fromDateField.value && fromDateField.value !== '__/__/____') {
                state.fromDateFilled = true;
            }
           
            // Check Quantity
            var quantityField = document.querySelector('input[id*="serviceQuantity"]');
            if (quantityField && quantityField.value && quantityField.value !== '') {
                state.quantityFilled = true;
            }
           
            // Check Days
            var quantityTypeContainer = document.querySelector('div[id*="quantityType"]');
            if (quantityTypeContainer && quantityTypeContainer.textContent.includes('Days')) {
                state.daysSelected = true;
            }
           
            return state;
        """)
       
        logger.info(" UI state check:")
        for field, status in ui_state.items():
            logger.info(f" - {field}: {'Filled in UI' if status else 'Not filled in UI'}")
       
        # Override tracking variables if UI shows fields are filled
        if ui_state.get('diagnosisFilled', False) and not diagnosis_filled:
            logger.info(" Overriding Diagnosis Code status based on UI state")
            diagnosis_filled = True
       
        if ui_state.get('procedureFilled', False) and not procedure_filled:
            logger.info(" Overriding Procedure Code status based on UI state")
            procedure_filled = True
       
        if ui_state.get('fromDateFilled', False) and not from_date_filled:
            logger.info(" Overriding From Date status based on UI state")
            from_date_filled = True
       
        if ui_state.get('quantityFilled', False) and not quantity_filled:
            logger.info(" Overriding Quantity status based on UI state")
            quantity_filled = True
       
        if ui_state.get('daysSelected', False) and not days_selected:
            logger.info(" Overriding Days selection status based on UI state")
            days_selected = True
       
        # Recalculate all_fields_filled
        all_fields_filled = diagnosis_filled and procedure_filled and from_date_filled and days_selected
   
    if all_fields_filled:
        logger.info(" All required fields are properly filled. Proceeding to click Next.")
       
        # ===== CLICK NEXT BUTTON =====
        try:
            logger.info(" Looking for Next button...")
           
            next_button = None
            try:
                next_button = driver.find_element(By.ID, "authWizardNextButton")
                logger.info(" Found Next button by ID")
            except:
                logger.info(" Could not find Next button by ID")
           
            if not next_button:
                try:
                    next_button = driver.find_element(By.XPATH, "//button[contains(text(), 'Next')]")
                    logger.info(" Found Next button by text")
                except:
                    logger.info(" Could not find Next button by text")
           
            if not next_button:
                # Try JavaScript to find it
                logger.info(" Using JavaScript to find Next button")
                next_button = driver.execute_script("""
                    // Try to find by ID
                    var button = document.getElementById('authWizardNextButton');
                    if (button) return button;
                   
                    // Try to find by text content
                    var buttons = document.querySelectorAll('button');
                    for (let btn of buttons) {
                        if (btn.textContent.includes('Next')) {
                            return btn;
                        }
                    }
                   
                    // Try to find by class
                    var nextButtons = document.querySelectorAll('.btn-primary');
                    for (let btn of nextButtons) {
                        if (btn.textContent.includes('Next')) {
                            return btn;
                        }
                    }
                   
                    return null;
                """)
           
            if next_button:
                # Highlight the button in screenshots
                driver.execute_script("arguments[0].style.border='5px solid blue'", next_button)
                time.sleep(1)
                take_screenshot(driver, "next_button_highlighted")
               
                # Click the Next button
                safe_click(driver, next_button, "Next button")
                logger.info(" Clicked Next button")
                time.sleep(3)
               
                # Switch back to default content
                driver.switch_to.default_content()
                take_screenshot(driver, "after_next_button_click")
                return True
            else:
                logger.error(" Could not find Next button")
               
                # Try with JavaScript as a fallback
                try:
                    logger.info(" Trying JavaScript to click Next button...")
                    driver.execute_script("""
                        // Try to find by ID first
                        var nextButton = document.getElementById('authWizardNextButton');
                       
                        // If not found by ID, try other methods
                        if (!nextButton) {
                            // Try to find by text content
                            var buttons = document.querySelectorAll('button');
                            for (var i = 0; i < buttons.length; i++) {
                                if (buttons[i].textContent.includes('Next')) {
                                    nextButton = buttons[i];
                                    break;
                                }
                            }
                        }
                       
                        // If found, click it
                        if (nextButton) {
                            nextButton.click();
                            return true;
                        }
                       
                        return false;
                    """)
                    logger.info(" Clicked Next button using JavaScript")
                    time.sleep(3)
                   
                    # Switch back to default content
                    driver.switch_to.default_content()
                    take_screenshot(driver, "after_js_next_button_click")
                    return True
                except Exception as e:
                    logger.error(f" All attempts to click Next button failed: {str(e)}")
                   
                    # Switch back to default content before returning
                    try:
                        driver.switch_to.default_content()
                    except:
                        pass
                   
                    return False
        except Exception as e:
            logger.error(f" Error clicking Next button: {str(e)}")
           
            # Switch back to default content before returning
            try:
                driver.switch_to.default_content()
            except:
                pass
           
            return False
    else:
        logger.error(" Cannot proceed to Next button because required fields are not properly filled:")
        if not diagnosis_filled:
            logger.error(" - Diagnosis Code is not filled")
        if not procedure_filled:
            logger.error(" - Procedure Code is not filled")
        if not from_date_filled:
            logger.error(" - From Date is not filled with value from database")
        if not days_selected:
            logger.error(" - Days is not selected for Quantity Type")
       
        # Switch back to default content before returning
        try:
            driver.switch_to.default_content()
        except:
            pass
       
        return False

def select_providers(driver):
    """Select providers using the most efficient approach based on terminal logs"""
    logger.info("\n Starting to select providers...")
    take_screenshot(driver, "before_provider_selection")

    # Make sure we're in the default content first
    try:
        driver.switch_to.default_content()
        logger.info(" Switched to default content")
    except Exception as e:
        logger.warning(f" Error switching to default content: {str(e)}")

    # Wait for the form to be fully loaded
    time.sleep(3)
   
    # Switch to iframe 2 as per previous pattern
    frames = driver.find_elements(By.TAG_NAME, "iframe")
    logger.info(f" Found {len(frames)} iframes on the page")
   
    if len(frames) >= 2:
        try:
            driver.switch_to.frame(frames[1])  # Switch to iframe 2 (index 1)
            logger.info(" Switched to iframe 2")
            take_screenshot(driver, "iframe_2_content_providers")
           
            # Based on the terminal logs, the handle_select2_field approach works best
            # Skip the inefficient approaches and go straight to this method
            logger.info(" Using handle_select2_field approach for provider selection")
           
            # Try for the first provider
            if handle_select2_field(driver, "Select a Provider", "KOLLIPARA"):
                logger.info(" Successfully selected first provider using handle_select2_field")
            else:
                logger.error(" Failed to select first provider")
                return False
           
            # Take a screenshot of the final state before clicking Next
            take_screenshot(driver, "before_clicking_next_after_providers")
           
            # Now click the Next button
            try:
                logger.info(" Looking for Next button...")
               
                # Try to find the Next button by ID first
                next_button = WebDriverWait(driver, TIMEOUT).until(
                    EC.element_to_be_clickable((By.ID, "authWizardNextButton"))
                )
                logger.info(" Found Next button by ID: authWizardNextButton")
               
                # Highlight the Next button in screenshots
                driver.execute_script("arguments[0].style.border='5px solid blue'", next_button)
                time.sleep(1)
                take_screenshot(driver, "next_button_highlighted")
               
                # Click the Next button using JavaScript for reliability
                driver.execute_script("arguments[0].click();", next_button)
                logger.info(" Clicked Next button after selecting providers")
               
                # Wait for the next page to load
                wait_for_page_load(driver)
                take_screenshot(driver, "after_next_button_click")
               
                logger.info(" ✓ Successfully proceeded to next page after selecting providers")
            except Exception as e:
                logger.error(f" Error clicking Next button: {str(e)}")
                return False
           
            # Switch back to default content
            driver.switch_to.default_content()
            logger.info(" Switched back to default content after selecting providers")
           
            logger.info(" Successfully selected providers and proceeded to next page")
            return True
           
        except Exception as e:
            logger.error(f" Error in iframe 2 while selecting providers: {str(e)}")
            logger.error(traceback.format_exc())
            driver.switch_to.default_content()
            return False
    else:
        logger.error(f" Not enough iframes found. Found only {len(frames)} iframes.")
        return False

# New function to handle the Next Steps button (from michuaetna.py)
def click_next_steps_button(driver):
    """Click the Next Steps button on the page after the final Next button"""
    logger.info("\n Starting to click Next Steps button...")
    take_screenshot(driver, "before_next_steps_button")

    # Make sure we're in the default content first
    try:
        driver.switch_to.default_content()
        logger.info(" Switched to default content")
    except Exception as e:
        logger.warning(f" Error switching to default content: {str(e)}")

    # Wait for the form to be fully loaded
    time.sleep(3)
   
    # Switch to iframe 2 as per previous pattern
    frames = driver.find_elements(By.TAG_NAME, "iframe")
    logger.info(f" Found {len(frames)} iframes on the page")
   
    if len(frames) >= 2:
        try:
            driver.switch_to.frame(frames[1])  # Switch to iframe 2 (index 1)
            logger.info(" Switched to iframe 2")
            take_screenshot(driver, "iframe_2_content_next_steps")
           
            # Based on the terminal logs, the direct ID approach works best
            try:
                next_steps_button = WebDriverWait(driver, TIMEOUT).until(
                    EC.element_to_be_clickable((By.ID, "nextStepsButton"))
                )
                logger.info(" Found Next Steps button by ID")
               
                # Highlight the button in screenshots
                driver.execute_script("arguments[0].style.border='5px solid blue'", next_steps_button)
                time.sleep(1)
                take_screenshot(driver, "next_steps_button_highlighted")
               
                # Click the Next Steps button
                safe_click(driver, next_steps_button, "Next Steps button")
                logger.info(" Clicked Next Steps button")
               
                # Wait for the next page to load
                wait_for_page_load(driver)
                take_screenshot(driver, "after_next_steps_button_click")
               
                logger.info(" ✓ Successfully clicked Next Steps button and proceeded to next page")
               
                # Switch back to default content
                driver.switch_to.default_content()
                return True
            except Exception as e:
                logger.error(f" Error clicking Next Steps button: {str(e)}")
                driver.switch_to.default_content()
                return False
        except Exception as e:
            logger.error(f" Error in iframe 2 while clicking Next Steps button: {str(e)}")
            logger.error(traceback.format_exc())
           
            # Switch back to default content before returning
            try:
                driver.switch_to.default_content()
            except:
                pass
           
            return False
    else:
        logger.error(f" Not enough iframes found. Found only {len(frames)} iframes.")
        return False

# New function to handle the second Next button (from michuaetna.py)
def click_second_next_button(driver):
    """Click the Next button on the page after the Next Steps button"""
    logger.info("\n Starting to click second Next button...")
    take_screenshot(driver, "before_second_next_button")

    # Make sure we're in the default content first
    try:
        driver.switch_to.default_content()
        logger.info(" Switched to default content")
    except Exception as e:
        logger.warning(f" Error switching to default content: {str(e)}")

    # Wait for the form to be fully loaded
    time.sleep(3)
   
    # Switch to iframe 2 as per previous pattern
    frames = driver.find_elements(By.TAG_NAME, "iframe")
    logger.info(f" Found {len(frames)} iframes on the page")
   
    if len(frames) >= 2:
        try:
            driver.switch_to.frame(frames[1])  # Switch to iframe 2 (index 1)
            logger.info(" Switched to iframe 2")
            take_screenshot(driver, "iframe_2_content_second_next")
           
            # Based on the terminal logs, the direct ID approach works best
            try:
                next_button = WebDriverWait(driver, TIMEOUT).until(
                    EC.element_to_be_clickable((By.ID, "authWizardNextButton"))
                )
                logger.info(" Found second Next button by ID")
               
                # Highlight the button in screenshots
                driver.execute_script("arguments[0].style.border='5px solid blue'", next_button)
                time.sleep(1)
                take_screenshot(driver, "second_next_button_highlighted")
               
                # Click the Next button
                safe_click(driver, next_button, "Second Next button")
                logger.info(" Clicked second Next button")
               
                # Wait for the next page to load
                wait_for_page_load(driver)
                take_screenshot(driver, "after_second_next_button_click")
               
                logger.info(" ✓ Successfully clicked second Next button and proceeded to next page")
               
                # Switch back to default content
                driver.switch_to.default_content()
                return True
            except Exception as e:
                logger.error(f" Error clicking second Next button: {str(e)}")
                driver.switch_to.default_content()
                return False
        except Exception as e:
            logger.error(f" Error in iframe 2 while clicking second Next button: {str(e)}")
            logger.error(traceback.format_exc())
           
            # Switch back to default content before returning
            try:
                driver.switch_to.default_content()
            except:
                pass
           
            return False
    else:
        logger.error(f" Not enough iframes found. Found only {len(frames)} iframes.")
        return False

# New function to handle the final Submit button (from michuaetna.py)
def click_submit_button(driver):
    """Click the Submit button on the final page"""
    logger.info("\n Starting to click Submit button...")
    take_screenshot(driver, "before_submit_button")

    # Make sure we're in the default content first
    try:
        driver.switch_to.default_content()
        logger.info(" Switched to default content")
    except Exception as e:
        logger.warning(f" Error switching to default content: {str(e)}")

    # Wait for the form to be fully loaded
    time.sleep(3)
   
    # Switch to iframe 2 as per previous pattern
    frames = driver.find_elements(By.TAG_NAME, "iframe")
    logger.info(f" Found {len(frames)} iframes on the page")
   
    if len(frames) >= 2:
        try:
            driver.switch_to.frame(frames[1])  # Switch to iframe 2 (index 1)
            logger.info(" Switched to iframe 2")
            take_screenshot(driver, "iframe_2_content_submit")
           
            # Based on the terminal logs, the direct ID approach works best
            try:
                submit_button = WebDriverWait(driver, TIMEOUT).until(
                    EC.element_to_be_clickable((By.ID, "authWizardNextButton"))
                )
                logger.info(" Found Submit button by ID")
               
                # Highlight the button in screenshots
                driver.execute_script("arguments[0].style.border='5px solid blue'", submit_button)
                time.sleep(1)
                take_screenshot(driver, "submit_button_highlighted")
               
                # Click the Submit button
                safe_click(driver, submit_button, "Submit button")
                logger.info(" Clicked Submit button")
               
                # Wait for the next page to load
                wait_for_page_load(driver)
                take_screenshot(driver, "after_submit_button_click")
               
                logger.info(" ✓ Successfully clicked Submit button and completed the workflow")
               
                # Switch back to default content
                driver.switch_to.default_content()
                return True
            except Exception as e:
                logger.error(f" Error clicking Submit button: {str(e)}")
                driver.switch_to.default_content()
                return False
        except Exception as e:
            logger.error(f" Error in iframe 2 while clicking Submit button: {str(e)}")
            logger.error(traceback.format_exc())
           
            # Switch back to default content before returning
            try:
                driver.switch_to.default_content()
            except:
                pass
           
            return False
    else:
        logger.error(f" Not enough iframes found. Found only {len(frames)} iframes.")
        return False

# New function to handle the final New Request button (from michuaetna.py)
def click_final_new_request_button(driver):
    """Click the New Request button after submission is complete"""
    logger.info("\n Starting to click final New Request button...")
    take_screenshot(driver, "before_final_new_request_button")

    # Wait for the page to fully load after submission
    wait_for_page_load(driver, LONG_TIMEOUT)
   
    # Wait for any submission result message to appear
    try:
        logger.info(" Waiting for submission result message...")
       
        # Generic locator for any result message
        result_message_locator = (By.XPATH, "//div[contains(@class, 'alert') or contains(@class, 'message')]")
       
        # Wait for any result message
        result_message = WebDriverWait(driver, VERY_LONG_TIMEOUT).until(
            EC.visibility_of_element_located(result_message_locator)
        )
       
        if result_message:
            message_text = result_message.text
            logger.info(f" Submission result message: {message_text}")
            take_screenshot(driver, "submission_result_message")
           
            # Additional wait after seeing the result message
            time.sleep(5)
    except Exception as e:
        logger.warning(f" Error waiting for submission result message: {str(e)}")
        logger.warning(" Continuing anyway...")
   
    # Make sure we're in the default content (not in an iframe)
    try:
        driver.switch_to.default_content()
        logger.info(" Switched to default content")
    except Exception as e:
        logger.warning(f" Error switching to default content: {str(e)}")
   
    # Now look for the New Request button in the main content
    try:
        logger.info(" Looking for New Request button in main content...")
       
        # Try to find the New Request button
        new_request_button = WebDriverWait(driver, TIMEOUT).until(
            EC.element_to_be_clickable(LOCATORS["new_request_button"])
        )
       
        # Highlight the button in screenshots
        driver.execute_script("arguments[0].style.border='5px solid blue'", new_request_button)
        time.sleep(1)
        take_screenshot(driver, "final_new_request_button_highlighted")
       
        # Click the New Request button
        safe_click(driver, new_request_button, "New Request button")
        logger.info(" Clicked New Request button")
       
        # Wait for the form to reload
        wait_for_page_load(driver, TIMEOUT)
        time.sleep(3)  # Additional wait to ensure form is fully loaded
        take_screenshot(driver, "after_new_request_button_click")
       
        # Verify the form is cleared by checking for empty fields
        try:
            # Switch to iframe 2 to check for fields
            frames = driver.find_elements(By.TAG_NAME, "iframe")
            if len(frames) >= 2:
                driver.switch_to.frame(frames[1])
               
                # Try to find Member ID field
                try:
                    member_id_field = driver.find_element(By.CSS_SELECTOR, "input#subscriber\\.memberId")
                    # Check if field is empty
                    if member_id_field.get_attribute("value") == "":
                        logger.info(" Form successfully cleared")
                    else:
                        logger.warning(f" Member ID field not empty: {member_id_field.get_attribute('value')}")
                except:
                    logger.warning(" Could not find Member ID field to verify form cleared")
               
                # Switch back to default content
                driver.switch_to.default_content()
            else:
                logger.warning(" Could not verify form cleared - not enough iframes")
        except Exception as e:
            logger.warning(f" Could not verify form cleared: {str(e)}")
            # Make sure we're back to default content
            try:
                driver.switch_to.default_content()
            except:
                pass
       
        logger.info(" ✓ Successfully clicked New Request button and started a new form")
        return True
    except Exception as e:
        logger.error(f" Error clicking New Request button: {str(e)}")
        logger.error(traceback.format_exc())
        take_screenshot(driver, "new_request_button_error")
       
        # Try fallback method - direct refresh
        try:
            logger.info(" Trying fallback - direct page refresh")
            driver.refresh()
            wait_for_page_load(driver, LONG_TIMEOUT)
            logger.info(" Direct refresh completed")
            take_screenshot(driver, "after_direct_refresh")
            return True
        except Exception as e:
            logger.error(f" Direct refresh failed: {str(e)}")
            return False

# ===============================
# MAIN EXECUTION - FIXED FOR FLASK
# ===============================
def main():
    logger.info("🚀 Starting Availity Authorization Workflow Script")

    # Initialize Chrome webdriver with improved setup
    driver = setup_chrome_driver()
    if not driver:
        logger.error("Failed to initialize Chrome driver. Please check the error messages above.")
        return False  # Return False instead of just return

    try:
        # Step 1: Navigate to Availity
        logger.info("\n Step 1: Navigating to Availity")
        if not navigate_to_url(driver, "https://www.availity.com/", "Availity homepage"):
            return False
       
        # Handle cookie popup if present
        try:
            cookie_btn = WebDriverWait(driver, 5).until(
                EC.element_to_be_clickable(LOCATORS["cookie_button"])
            )
            cookie_btn.click()
            logger.info(" Accepted cookies")
        except:
            logger.info(" No cookie popup or already accepted")
       
        # Step 2: Click on Login link
        logger.info("\nStep 2: Clicking on Essentials Login")
        driver.execute_script("window.scrollTo(0, 0);")
        login_link = wait_for_and_find_element(driver, LOCATORS["login_link"], "login link")
        if not login_link:
            logger.error(" Could not find login link")
            return False
       
        if not safe_click(driver, login_link, "Essentials Login link"):
            logger.error(" Failed to click login link")
            return False
       
        # Wait for login page to load
        wait_for_page_load(driver)
       
        # Step 3: Login with credentials
        logger.info("\nStep 3: Entering login credentials")
        username_field = wait_for_and_find_element(driver, LOCATORS["username_input"], "username field")
        password_field = wait_for_and_find_element(driver, LOCATORS["password_input"], "password field")
        sign_in_button = wait_for_and_find_element(driver, LOCATORS["sign_in_button"], "sign in button")
       
        if not all([username_field, password_field, sign_in_button]):
            logger.error(" Login form elements not found")
            return False
       
        try:
            username_field.clear()
            username_field.send_keys(EMAIL)
            password_field.clear()
            password_field.send_keys(PASSWORD)
           
            if not safe_click(driver, sign_in_button, "Sign In button"):
                logger.error(" Failed to click Sign In button")
                return False
               
            logger.info(" Login form submitted")
        except Exception as e:
            logger.error(f" Login failed: {str(e)}")
            take_screenshot(driver, "login_failed")
            return False
       
        # Step 4: Handle 2FA selection
        logger.info("\n📋 Step 4: Setting up 2FA")
        try:
            sms_option = WebDriverWait(driver, TIMEOUT).until(
                EC.presence_of_element_located(LOCATORS["sms_option"])
            )
           
            if not safe_click(driver, sms_option, "SMS option"):
                logger.warning(" Failed to click SMS option, trying to continue anyway")
           
            continue_button = WebDriverWait(driver, TIMEOUT).until(
                EC.element_to_be_clickable(LOCATORS["continue_button"])
            )
           
            if not safe_click(driver, continue_button, "Continue button"):
                logger.error(" Failed to click Continue button")
                return False
               
            logger.info(" Submitted 2FA method")
        except Exception as e:
            logger.warning(f" 2FA method selection failed: {str(e)}")
            logger.warning("Continuing anyway - 2FA screens may vary")
       
        # Step 5: Handle MFA challenge with integrated system
        logger.info("\n📋 Step 5: Handling MFA Challenge")
        time.sleep(3)  # Wait for MFA page to load
        
        # Check if we're on MFA page
        try:
            mfa_input = driver.find_element(By.XPATH, "//input[@placeholder='Code' or @name='code' or @type='text']")
            if mfa_input.is_displayed():
                logger.info("MFA challenge detected")
                if not handle_mfa_challenge(driver):
                    logger.error("MFA challenge failed")
                    return False
        except:
            logger.info("No MFA challenge detected, continuing...")
        
        # Wait for login to complete
        initial_url = driver.current_url
        start_time = time.time()
        
        while time.time() - start_time < 300:
            if driver.current_url != initial_url:
                break
            time.sleep(3)
        
        wait_for_page_load(driver)
        logger.info("2FA process completed!")
       
        # Step 6: Wait for dashboard to load
        logger.info("\n Step 6: Waiting for dashboard to load")
        wait_for_page_load(driver, LONG_TIMEOUT)
        logger.info(f"Current URL: {driver.current_url}")
        take_screenshot(driver, "dashboard")
       
        # Step 7: Navigate to Patient Registration
        logger.info("\n Step 7: Navigating to Patient Registration")
        patient_reg_element = wait_for_and_find_element(
            driver, LOCATORS["patient_registration"], "Patient Registration", TIMEOUT
        )
       
        if not patient_reg_element:
            logger.error(" Could not find Patient Registration link")
            return False
       
        # Hover and click
        ActionChains(driver).move_to_element(patient_reg_element).perform()
        logger.info(" Hovered over Patient Registration")
        time.sleep(2)
       
        if safe_click(driver, patient_reg_element, "Patient Registration"):
            logger.info(" Clicked on Patient Registration")
            time.sleep(3)
            wait_for_page_load(driver)
            take_screenshot(driver, "after_patient_reg_click")
           
            # Step 8: Click on Authorizations & Referrals
            logger.info("\n Step 8: Looking for Authorizations & Referrals")
            auth_ref_element = wait_for_and_find_element(
                driver, LOCATORS["auth_and_referrals"], "Authorizations & Referrals", TIMEOUT
            )
           
            if not auth_ref_element:
                logger.error(" Could not find Authorizations & Referrals")
                # Try alternative locator
                try:
                    logger.info(" Trying alternative locator for Authorizations & Referrals")
                    auth_ref_element = WebDriverWait(driver, TIMEOUT).until(
                        EC.element_to_be_clickable((By.XPATH, "//a[contains(text(), 'Authorizations & Referrals')]"))
                    )
                    logger.info(" Found Authorizations & Referrals with alternative locator")
                except Exception as e:
                    logger.error(f" Alternative locator also failed: {str(e)}")
                    return False
           
            if safe_click(driver, auth_ref_element, "Authorizations & Referrals"):
                logger.info(" Clicked on Authorizations & Referrals")
                time.sleep(3)
                wait_for_page_load(driver, LONG_TIMEOUT)
                take_screenshot(driver, "auth_referrals_page")
               
                # Create patient record from command line arguments
                patient_record = {
                    'Member ID': member_id,
                    'Patient Date of Birth': format_date_for_form(patient_dob),
                    'Patient Name': patient_name,
                    'Provider Name': provider_name,
                    'NPI Number': provider_npi,
                    'Procedure Code': procedure_code,
                    'Diagnosis Code': diagnosis_code,
                    'From Date': format_date_for_form(from_date),
                    'To Date': format_date_for_form(to_date),
                    'Primary Insurance': primary_insurance
                }
               
                logger.info(f"\n Processing patient record: {patient_record}")
               
                try:
                    # Step 9: Click on Authorization Request link
                    logger.info(f"\n Step 9: Clicking on Authorization Request link")
                    if click_authorization_request(driver):
                        logger.info(" Successfully navigated to Authorization Request page")
                       
                        # Step 10: Fill out the Authorization form
                        logger.info(f"\n Step 10: Filling out Authorization form")
                        if fill_authorization_form(driver):
                            logger.info(" Authorization form filled successfully")
                           
                            # Step 11: Fill out the Patient Information form
                            logger.info(f"\n Step 11: Filling out Patient Information form")
                            if fill_patient_info_form(driver, patient_record):
                                logger.info(" Patient Information form filled successfully")  
                               
                                # Step 12: Fill out the Diagnosis and Procedure form
                                logger.info(f"\n Step 12: Filling out Diagnosis and Procedure form")
                                if fill_diagnosis_procedure_form(driver, patient_record):
                                    logger.info(" Diagnosis and Procedure form filled successfully")
                                   
                                    # Step 13: Select providers
                                    logger.info(f"\n Step 13: Selecting providers")
                                    if select_providers(driver):
                                        logger.info(" Providers selected successfully")
                                       
                                        # Step 14: Click Next Steps button
                                        logger.info(f"\n Step 14: Clicking Next Steps button")
                                        if click_next_steps_button(driver):
                                            logger.info(" Next Steps button clicked successfully")
                                           
                                            # Step 15: Click second Next button
                                            logger.info(f"\n Step 15: Clicking second Next button")
                                            if click_second_next_button(driver):
                                                logger.info(" Second Next button clicked successfully")
                                               
                                                # Step 16: Click Submit button
                                                logger.info(f"\n Step 16: Clicking Submit button")
                                                if click_submit_button(driver):
                                                    logger.info(" Submit button clicked successfully")
                                                   
                                                    # Step 17: Click final New Request button
                                                    logger.info(f"\n Step 17: Clicking final New Request button")
                                                    if click_final_new_request_button(driver):
                                                        logger.info(" Final New Request button clicked successfully")
                                                        logger.info("Patient record processed successfully with complete workflow")
                                                        return True  # Success
                                                    else:
                                                        logger.error(" Failed to click final New Request button")
                                                        return False
                                                else:
                                                    logger.error(" Failed to click Submit button")
                                                    return False
                                            else:
                                                logger.error(" Failed to click second Next button")
                                                return False
                                        else:
                                            logger.error(" Failed to click Next Steps button")
                                            return False
                                    else:
                                        logger.error(" Failed to select providers")
                                        return False
                                else:
                                    logger.error(" Failed to fill Diagnosis and Procedure form")
                                    return False
                            else:
                                logger.error(" Failed to fill Patient Information form")
                                return False
                        else:
                            logger.error(" Failed to fill Authorization form")
                            return False
                    else:
                        logger.error(" Failed to navigate to Authorization Request page")
                        return False
                       
                except Exception as e:
                    logger.error(f" Unexpected error processing patient record: {str(e)}")
                    logger.error(traceback.format_exc())
                    return False
               
            else:
                logger.error(" Failed to click on Authorizations & Referrals")
                return False
        else:
            logger.error(" Failed to click on Patient Registration")
            return False
           
    except Exception as e:
        logger.error(f" Unexpected error: {str(e)}")
        logger.error(traceback.format_exc())
        take_screenshot(driver, "unexpected_error")
        return False
    finally:
        # Always close the driver when running from Flask
        try:
            logger.info("\n Closing browser...")
            driver.quit()
        except Exception as e:
            logger.error(f"Error closing driver: {str(e)}")

    logger.info("\n Script execution completed successfully")
    return True

if __name__ == "__main__":
    main()
