import time
import os
import json
import sys
import requests
from selenium import webdriver
from selenium.webdriver.chrome.service import Service
from selenium.webdriver.chrome.options import Options
from selenium.webdriver.common.by import By
from selenium.webdriver.support.ui import WebDriverWait
from selenium.webdriver.support import expected_conditions as EC
from selenium.webdriver.common.keys import Keys
from selenium.webdriver.common.action_chains import ActionChains
from webdriver_manager.chrome import ChromeDriverManager
from selenium.common.exceptions import StaleElementReferenceException, NoSuchElementException
from dotenv import load_dotenv

load_dotenv()

class EligibilityBot:
    def __init__(self):
        self.driver = None
        self.timeout = 20
        self.email = os.getenv('AVAILITY_EMAIL')
        self.password = os.getenv('AVAILITY_PASSWORD')
        self.mfa_session_id = None
        self.flask_base_url = "http://localhost:5000"  # Adjust if Flask runs on different port

    def request_mfa_session(self):
        """Request a new MFA session from Flask backend"""
        try:
            response = requests.post(f"{self.flask_base_url}/mfa-request", 
                                   json={"script_type": "eligibility"}, 
                                   timeout=10)
            if response.status_code == 200:
                data = response.json()
                self.mfa_session_id = data.get('session_id')
                print(f"MFA session requested: {self.mfa_session_id}")
                return True
            else:
                print(f"Failed to request MFA session: {response.status_code}")
                return False
        except Exception as e:
            print(f"Error requesting MFA session: {str(e)}")
            return False

    def wait_for_mfa_code(self, timeout=300):
        """Poll Flask backend for MFA code"""
        if not self.mfa_session_id:
            print("No MFA session ID available")
            return None
            
        start_time = time.time()
        while time.time() - start_time < timeout:
            try:
                response = requests.get(f"{self.flask_base_url}/mfa-check/{self.mfa_session_id}", 
                                      timeout=10)
                if response.status_code == 200:
                    data = response.json()
                    if data.get('status') == 'completed':
                        mfa_code = data.get('code')
                        print(f"Received MFA code: {mfa_code}")
                        return mfa_code
                    elif data.get('status') == 'expired':
                        print("MFA session expired")
                        return None
                        
                # Wait before next poll
                time.sleep(3)
                
            except Exception as e:
                print(f"Error checking MFA code: {str(e)}")
                time.sleep(3)
                
        print("Timeout waiting for MFA code")
        return None

    def handle_mfa_challenge(self):
        """Handle MFA challenge by requesting session and waiting for code"""
        print("MFA challenge detected, requesting user input...")
        
        # Request MFA session
        if not self.request_mfa_session():
            print("Failed to request MFA session")
            return False
            
        # Wait for user to enter code
        mfa_code = self.wait_for_mfa_code()
        if not mfa_code:
            print("Failed to get MFA code")
            return False
            
        # Enter the code
        try:
            # Look for MFA input field
            mfa_input = WebDriverWait(self.driver, 10).until(
                EC.presence_of_element_located((By.XPATH, "//input[@placeholder='Code' or @name='code' or @type='text']"))
            )
            
            mfa_input.clear()
            mfa_input.send_keys(mfa_code)
            
            # Look for submit button
            submit_button = self.driver.find_element(By.XPATH, "//button[contains(text(), 'Continue') or contains(text(), 'Submit') or contains(text(), 'Verify')]")
            submit_button.click()
            
            print("MFA code entered successfully")
            return True
            
        except Exception as e:
            print(f"Error entering MFA code: {str(e)}")
            return False

    def setup_driver(self):
        options = Options()
        options.add_argument("--start-maximized")
        options.add_argument("--disable-extensions")
        options.add_argument("--disable-gpu")
        options.add_argument("--no-sandbox")
        options.add_argument("--disable-dev-shm-usage")
        
        service = Service(ChromeDriverManager().install())
        self.driver = webdriver.Chrome(service=service, options=options)
        return self.driver

    def wait_for_page_load(self):
        WebDriverWait(self.driver, self.timeout).until(
            lambda d: d.execute_script("return document.readyState") == "complete"
        )
        time.sleep(2)

    def safe_click(self, element):
        try:
            self.driver.execute_script("arguments[0].scrollIntoView({block: 'center'});", element)
            time.sleep(1)
            element.click()
            return True
        except:
            try:
                self.driver.execute_script("arguments[0].click();", element)
                return True
            except:
                try:
                    ActionChains(self.driver).move_to_element(element).click().perform()
                    return True
                except:
                    return False

    def safe_get_element_info(self, element):
        """Safely extract element information without causing stale element errors"""
        try:
            # Get all information in one go to avoid multiple DOM queries
            element_info = self.driver.execute_script("""
                var element = arguments[0];
                if (!element) return null;
                
                var computedStyle = window.getComputedStyle(element);
                var rect = element.getBoundingClientRect();
                
                return {
                    text: element.textContent || element.innerText || '',
                    tagName: element.tagName,
                    className: element.className,
                    id: element.id,
                    backgroundColor: computedStyle.backgroundColor,
                    borderColor: computedStyle.borderColor,
                    color: computedStyle.color,
                    display: computedStyle.display,
                    visibility: computedStyle.visibility,
                    isVisible: rect.width > 0 && rect.height > 0 && computedStyle.visibility !== 'hidden',
                    outerHTML: element.outerHTML.substring(0, 200) // First 200 chars for debugging
                };
            """, element)
            
            return element_info
        except Exception as e:
            print(f"Error getting element info: {str(e)}")
            return None

    def rgb_to_hex(self, rgb_string):
        """Convert RGB string like 'rgb(255, 206, 170)' to hex"""
        try:
            if not rgb_string or rgb_string == 'rgba(0, 0, 0, 0)' or rgb_string == 'transparent':
                return None
            
            # Handle rgba format
            if rgb_string.startswith('rgba'):
                rgb_values = rgb_string.replace('rgba(', '').replace(')', '').split(',')
                if len(rgb_values) >= 3:
                    r, g, b = [int(float(x.strip())) for x in rgb_values[:3]]
                    return f"#{r:02x}{g:02x}{b:02x}".upper()
            # Handle rgb format
            elif rgb_string.startswith('rgb'):
                rgb_values = rgb_string.replace('rgb(', '').replace(')', '').split(',')
                if len(rgb_values) >= 3:
                    r, g, b = [int(float(x.strip())) for x in rgb_values]
                    return f"#{r:02x}{g:02x}{b:02x}".upper()
            
            return None
        except Exception as e:
            print(f"Error converting RGB to hex: {str(e)}")
            return None

    def is_invalid_color(self, color_hex):
        """Check if color indicates invalid/error status"""
        if not color_hex:
            return False
        
        # Define invalid color patterns (yellow/amber/orange tones)
        invalid_colors = [
            "#FFCEA", "#FFCEAA", "#FFD4AA", "#F0E68C", "#FFE4B5", 
            "#FFEAA7", "#FFF2CC", "#FFFACD", "#FFFFE0", "#FFFFF0",
            "#FDF5E6", "#FAF0E6", "#FFEFD5", "#FFE4E1"
        ]
        
        # Check for exact matches or similar colors
        for invalid_color in invalid_colors:
            if self.is_color_similar(color_hex, invalid_color):
                return True
        
        return False

    def is_valid_color(self, color_hex):
        """Check if color indicates valid/success status"""
        if not color_hex:
            return False
        
        # Define valid color patterns (green tones and blue tones for active coverage)
        valid_colors = [
            "#90EE90", "#98FB98", "#00FF00", "#32CD32", "#228B22", 
            "#008000", "#00C851", "#4CAF50", "#D4EDDA", "#DFF0D8",
            # Blue tones for "Active Coverage" badges
            "#007BFF", "#0056B3", "#004085", "#CCE5FF", "#B3D9FF",
            "#E3F2FD", "#BBDEFB", "#90CAF9", "#64B5F6", "#42A5F5"
        ]
        
        # Check for exact matches or similar colors
        for valid_color in valid_colors:
            if self.is_color_similar(color_hex, valid_color):
                return True
        
        return False

    def is_inactive_color(self, color_hex):
        """Check if color indicates inactive status (red tones)"""
        if not color_hex:
            return False
        
        # Define inactive color patterns (red tones)
        inactive_colors = [
            "#FF0000", "#DC3545", "#C82333", "#BD2130", "#B52D3A",
            "#A71E2A", "#8B0000", "#CD5C5C", "#F8D7DA", "#F5C6CB",
            "#FFEBEE", "#FFCDD2", "#EF9A9A", "#E57373", "#EF5350"
        ]
        
        # Check for exact matches or similar colors
        for inactive_color in inactive_colors:
            if self.is_color_similar(color_hex, inactive_color):
                return True
        
        return False

    def is_color_similar(self, color1, color2, tolerance=30):
        """Check if two hex colors are similar within tolerance"""
        try:
            if not color1 or not color2:
                return False
            
            # Remove # if present
            color1 = color1.lstrip('#')
            color2 = color2.lstrip('#')
            
            # Convert to RGB
            r1, g1, b1 = int(color1[0:2], 16), int(color1[2:4], 16), int(color1[4:6], 16)
            r2, g2, b2 = int(color2[0:2], 16), int(color2[2:4], 16), int(color2[4:6], 16)
            
            # Calculate color distance
            distance = abs(r1 - r2) + abs(g1 - g2) + abs(b1 - b2)
            return distance <= tolerance
        except:
            return False

    def login_to_availity(self):
        self.driver.get("https://www.availity.com/")
        self.wait_for_page_load()
        
        try:
            cookie_btn = WebDriverWait(self.driver, 5).until(
                EC.element_to_be_clickable((By.XPATH, "//button[contains(text(), 'Accept All Cookies')]"))
            )
            self.safe_click(cookie_btn)
        except:
            pass
        
        login_link = WebDriverWait(self.driver, self.timeout).until(
            EC.element_to_be_clickable((By.CSS_SELECTOR, "a[href='https://apps.availity.com/web/onboarding/availity-fr-ui/']"))
        )
        self.safe_click(login_link)
        self.wait_for_page_load()
        
        username_field = WebDriverWait(self.driver, self.timeout).until(
            EC.presence_of_element_located((By.ID, "userId"))
        )
        password_field = self.driver.find_element(By.ID, "password")
        sign_in_btn = self.driver.find_element(By.XPATH, "//button[contains(text(), 'Sign In')]")
        
        username_field.clear()
        username_field.send_keys(self.email)
        password_field.clear()
        password_field.send_keys(self.password)
        self.safe_click(sign_in_btn)
        
        try:
            sms_option = WebDriverWait(self.driver, self.timeout).until(
                EC.presence_of_element_located((By.XPATH, "//label[contains(., 'Authenticate me using my Authenticator app')]"))
            )
            self.safe_click(sms_option)
            
            continue_btn = WebDriverWait(self.driver, self.timeout).until(
                EC.element_to_be_clickable((By.XPATH, "//button[contains(text(), 'Continue')]"))
            )
            self.safe_click(continue_btn)
        except:
            pass
        
        # Handle MFA challenge
        print("Checking for MFA challenge...")
        time.sleep(3)  # Wait for MFA page to load
        
        # Check if we're on MFA page
        try:
            mfa_input = self.driver.find_element(By.XPATH, "//input[@placeholder='Code' or @name='code' or @type='text']")
            if mfa_input.is_displayed():
                print("MFA challenge detected")
                if not self.handle_mfa_challenge():
                    print("MFA challenge failed")
                    return False
        except:
            print("No MFA challenge detected, continuing...")
        
        # Wait for login to complete
        initial_url = self.driver.current_url
        start_time = time.time()
        
        while time.time() - start_time < 300:
            if self.driver.current_url != initial_url:
                break
            time.sleep(3)
        
        self.wait_for_page_load()

    def navigate_to_eligibility(self):
        patient_reg = WebDriverWait(self.driver, self.timeout).until(
            EC.element_to_be_clickable((By.XPATH, "//a[contains(text(), 'Patient Registration')]"))
        )
        self.safe_click(patient_reg)
        self.wait_for_page_load()
        
        eligibility_link = WebDriverWait(self.driver, self.timeout).until(
            EC.element_to_be_clickable((By.XPATH, "//div[contains(@class, 'media-body') and contains(., 'Eligibility and Benefits Inquiry')]"))
        )
        self.safe_click(eligibility_link)
        self.wait_for_page_load()

    def fill_payer(self, payer_name):
        self.driver.switch_to.default_content()
        time.sleep(3)
        
        frames = self.driver.find_elements(By.TAG_NAME, "iframe")
        if len(frames) >= 2:
            self.driver.switch_to.frame(frames[1])
            
            payer_field = WebDriverWait(self.driver, 10).until(
                EC.presence_of_element_located((By.CSS_SELECTOR, "input[id*='payer']"))
            )
            
            payer_field.clear()
            time.sleep(0.5)
            ActionChains(self.driver).move_to_element(payer_field).click().perform()
            time.sleep(0.5)
            
            for char in payer_name:
                payer_field.send_keys(char)
                time.sleep(0.1)
            
            time.sleep(1)
            
            try:
                first_item = self.driver.find_element(By.XPATH, "(//div[contains(@class, 'dropdown-item') or contains(@class, 'option') or @role='option'])[1]")
                self.safe_click(first_item)
            except:
                ActionChains(self.driver).send_keys(Keys.DOWN).perform()
                time.sleep(0.5)
                ActionChains(self.driver).send_keys(Keys.ENTER).perform()
            
            self.driver.switch_to.default_content()
            return True
        return False

    def fill_provider(self, provider_name):
        frames = self.driver.find_elements(By.TAG_NAME, "iframe")
        
        for frame in frames:
            try:
                self.driver.switch_to.frame(frame)
                
                provider_field = self.driver.find_element(By.CSS_SELECTOR, "input#provider")
                
                provider_field.clear()
                time.sleep(0.5)
                ActionChains(self.driver).move_to_element(provider_field).click().perform()
                time.sleep(0.5)
                
                for char in provider_name:
                    provider_field.send_keys(char)
                    time.sleep(0.1)
                
                time.sleep(1)
                
                ActionChains(self.driver).send_keys(Keys.ARROW_DOWN).perform()
                time.sleep(0.5)
                ActionChains(self.driver).send_keys(Keys.ENTER).perform()
                time.sleep(1)
                
                self.driver.switch_to.default_content()
                return True
            except:
                self.driver.switch_to.default_content()
                continue
        return False

    def fill_patient_data(self, member_id, dob):
        """Fill patient data using member_id instead of patient_id"""
        frames = self.driver.find_elements(By.TAG_NAME, "iframe")
        if len(frames) >= 2:
            self.driver.switch_to.frame(frames[1])
            
            member_id_field = self.driver.execute_script("""
                var inputs = document.querySelectorAll('input[type="text"]');
                
                for (let input of inputs) {
                    let parentDiv = input.closest('div');
                    if (parentDiv) {
                        let errorMsg = parentDiv.querySelector('.invalid-feedback');
                        if (errorMsg && (errorMsg.textContent.includes('Patient ID') || errorMsg.textContent.includes('Member ID'))) {
                            return input;
                        }
                    }
                    
                    let nearbyText = input.parentElement.textContent || '';
                    if (nearbyText.includes('Patient ID') || nearbyText.includes('Member ID')) {
                        return input;
                    }
                }
                
                for (let input of inputs) {
                    let computedStyle = window.getComputedStyle(input);
                    if (computedStyle.borderColor.includes('255') || 
                        input.classList.contains('is-invalid') || 
                        input.classList.contains('av-invalid')) {
                        return input;
                    }
                }
                
                return null;
            """)
            
            if member_id_field:
                member_id_field.clear()
                time.sleep(0.5)
                ActionChains(self.driver).move_to_element(member_id_field).click().perform()
                time.sleep(0.5)
                
                for char in str(member_id):
                    member_id_field.send_keys(char)
                    time.sleep(0.1)
            
            try:
                dob_field = WebDriverWait(self.driver, 5).until(
                    EC.presence_of_element_located((By.XPATH, "//input[contains(@placeholder, 'mm/dd/yyyy') or contains(@aria-label, 'Date of Birth')]"))
                )
                
                dob_field.clear()
                time.sleep(0.5)
                ActionChains(self.driver).move_to_element(dob_field).click().perform()
                time.sleep(0.5)
                
                for char in dob:
                    dob_field.send_keys(char)
                    time.sleep(0.1)
                
                dob_field.send_keys(Keys.TAB)
            except:
                pass
            
            self.driver.switch_to.default_content()
            return True
        return False

    def fill_service_type_and_submit(self, service_type):
        service_parts = service_type.split(" - ")
        service_name = service_parts[0]
        service_code = service_parts[1] if len(service_parts) > 1 else ""
        
        self.driver.switch_to.default_content()
        
        frames = self.driver.find_elements(By.TAG_NAME, "iframe")
        if len(frames) >= 2:
            try:
                self.driver.switch_to.frame(frames[1])
                
                try:
                    service_type_field = None
                    
                    try:
                        service_type_field = self.driver.find_element(By.XPATH, 
                            "//label[contains(@for, 'serviceType') or contains(text(), 'Benefit') or contains(text(), 'Service Type')]/following::div[contains(@class, 'av-select')]//input")
                    except:
                        pass
                    
                    if not service_type_field:
                        try:
                            service_type_field = self.driver.find_element(By.XPATH, "//div[contains(@class, 'av-select')]//input")
                        except:
                            pass
                    
                    if not service_type_field:
                        try:
                            inputs = self.driver.find_elements(By.CSS_SELECTOR, "input[role='combobox'], input.form-control")
                            for input_field in inputs:
                                field_id = input_field.get_attribute("id") or ""
                                if "payer" not in field_id.lower() and "provider" not in field_id.lower():
                                    service_type_field = input_field
                                    break
                        except:
                            pass
                    
                    if service_type_field:
                        self.driver.execute_script("arguments[0].style.border='5px solid green'", service_type_field)
                        time.sleep(1)
                        
                        service_type_field.clear()
                        time.sleep(0.5)
                        
                        ActionChains(self.driver).move_to_element(service_type_field).click().perform()
                        time.sleep(0.5)
                        
                        for char in service_name:
                            service_type_field.send_keys(char)
                            time.sleep(0.3)
                        
                        time.sleep(2)
                        
                        try:
                            option = WebDriverWait(self.driver, 5).until(
                                EC.element_to_be_clickable((By.XPATH, f"//div[contains(text(), '{service_type}')]"))
                            )
                            self.safe_click(option)
                        except:
                            ActionChains(self.driver).send_keys(Keys.ARROW_DOWN).perform()
                            time.sleep(0.5)
                            ActionChains(self.driver).send_keys(Keys.ENTER).perform()
                        
                        time.sleep(1)
                        
                        submit_button = WebDriverWait(self.driver, 5).until(
                            EC.element_to_be_clickable((By.XPATH, "//button[@type='submit' and contains(@class, 'MuiButton-containedPrimary') and contains(text(), 'Submit')]"))
                        )
                        self.safe_click(submit_button)
                        time.sleep(3)
                        
                        self.driver.switch_to.default_content()
                        return True
                    else:
                        pass
                        
                except Exception as e:
                    pass
                    
            except Exception as e:
                pass
                self.driver.switch_to.default_content()
        else:
            pass
        
        self.driver.switch_to.default_content()
        return False

    def check_eligibility_response(self):
        """
        Enhanced method to check eligibility response with support for:
        - Active Coverage (flag = 1)
        - Member Status Inactive (flag = 0) 
        - Invalid cases (flag = -1)
        """
        print("Waiting for eligibility response after form submission...")
        
        self.wait_for_page_load()
        time.sleep(5)
        
        try:
            self.driver.switch_to.default_content()
            
            # Use JavaScript to find and analyze elements to avoid stale element issues
            element_data = self.driver.execute_script("""
                var results = [];
                
                // Define selectors to search for
                var selectors = [
                    'div[class*="alert"]',
                    'div[class*="notification"]', 
                    'div[class*="message"]',
                    'div[class*="banner"]',
                    'div[class*="MuiAlert"]',
                    'div[class*="error"]',
                    'div[class*="success"]',
                    'div[class*="warning"]',
                    'div[class*="status"]',
                    'div[class*="badge"]',
                    'span[class*="badge"]',
                    'div[class*="coverage"]',
                    'span[class*="coverage"]'
                ];
                
                // Search in main document
                selectors.forEach(function(selector) {
                    var elements = document.querySelectorAll(selector);
                    elements.forEach(function(element) {
                        var text = element.textContent || element.innerText || '';
                        if (text.trim().length > 2) { // Filter out very short texts
                            var computedStyle = window.getComputedStyle(element);
                            var rect = element.getBoundingClientRect();
                            
                            if (rect.width > 0 && rect.height > 0) { // Element is visible
                                results.push({
                                    text: text.trim(),
                                    backgroundColor: computedStyle.backgroundColor,
                                    borderColor: computedStyle.borderColor,
                                    color: computedStyle.color,
                                    className: element.className,
                                    tagName: element.tagName,
                                    location: 'main_page'
                                });
                            }
                        }
                    });
                });
                
                return results;
            """)
            
            # Also check iframes
            frames = self.driver.find_elements(By.TAG_NAME, "iframe")
            for i, frame in enumerate(frames):
                try:
                    self.driver.switch_to.frame(frame)
                    
                    iframe_data = self.driver.execute_script("""
                        var results = [];
                        
                        var selectors = [
                            'div[class*="alert"]',
                            'div[class*="notification"]', 
                            'div[class*="message"]',
                            'div[class*="banner"]',
                            'div[class*="MuiAlert"]',
                            'div[class*="error"]',
                            'div[class*="success"]',
                            'div[class*="warning"]',
                            'div[class*="status"]',
                            'div[class*="badge"]',
                            'span[class*="badge"]',
                            'div[class*="coverage"]',
                            'span[class*="coverage"]'
                        ];
                        
                        selectors.forEach(function(selector) {
                            var elements = document.querySelectorAll(selector);
                            elements.forEach(function(element) {
                                var text = element.textContent || element.innerText || '';
                                if (text.trim().length > 2) {
                                    var computedStyle = window.getComputedStyle(element);
                                    var rect = element.getBoundingClientRect();
                                    
                                    if (rect.width > 0 && rect.height > 0) {
                                        results.push({
                                            text: text.trim(),
                                            backgroundColor: computedStyle.backgroundColor,
                                            borderColor: computedStyle.borderColor,
                                            color: computedStyle.color,
                                            className: element.className,
                                            tagName: element.tagName,
                                            location: 'iframe_""" + str(i+1) + """'
                                        });
                                    }
                                }
                            });
                        });
                        
                        return results;
                    """)
                    
                    element_data.extend(iframe_data)
                    
                    self.driver.switch_to.default_content()
                    
                except Exception as e:
                    self.driver.switch_to.default_content()
                    continue
            
            # Analyze collected element data with enhanced logic
            print(f"Found {len(element_data)} elements to analyze")
            
            for data in element_data:
                text = data['text']
                bg_color = data['backgroundColor']
                location = data['location']
                
                # Convert background color to hex
                bg_hex = self.rgb_to_hex(bg_color)
                
                # Text analysis
                text_lower = text.lower()
                
                print(f"Analyzing element: '{text}' with background: {bg_color} ({bg_hex})")
                
                # Enhanced text patterns for different scenarios
                
                # Active Coverage patterns (flag = 1)
                active_coverage_patterns = [
                    "active coverage",
                    "coverage active", 
                    "patient eligible",
                    "eligible",
                    "benefits available",
                    "valid coverage",
                    "approved",
                    "coverage is active"
                ]
                
                # Member Status Inactive patterns (flag = 0)
                inactive_status_patterns = [
                    "member status inactive",
                    "inactive",
                    "coverage inactive",
                    "status: inactive",
                    "member inactive",
                    "coverage expired",
                    "not active"
                ]
                
                # Invalid/Error patterns (flag = -1)
                invalid_patterns = [
                    "invalid/missing subscriber",
                    "invalid/missing insured id", 
                    "birth date does not match",
                    "please correct and resubmit",
                    "not eligible",
                    "invalid member id",
                    "error",
                    "incorrect",
                    "invalid patient",
                    "member not found"
                ]
                
                # Check text patterns
                text_indicates_active = any(pattern in text_lower for pattern in active_coverage_patterns)
                text_indicates_inactive = any(pattern in text_lower for pattern in inactive_status_patterns)
                text_indicates_invalid = any(pattern in text_lower for pattern in invalid_patterns)
                
                # Enhanced color analysis
                color_indicates_active = self.is_valid_color(bg_hex) if bg_hex else False
                color_indicates_inactive = self.is_inactive_color(bg_hex) if bg_hex else False
                color_indicates_invalid = self.is_invalid_color(bg_hex) if bg_hex else False
                
                # Decision logic with priority: Text patterns first, then color
                if text_indicates_active or (color_indicates_active and not text_indicates_inactive and not text_indicates_invalid):
                    print(f"ELIGIBILITY RESULT: ACTIVE COVERAGE")
                    return {
                        "status": "ACTIVE_COVERAGE",
                        "message": text,
                        "flag": 1,
                        "success": True,
                        "background_color": bg_color,
                        "background_hex": bg_hex,
                        "detection_method": "Text-based" if text_indicates_active else "Color-based",
                        "location": location
                    }
                elif text_indicates_inactive or (color_indicates_inactive and not text_indicates_invalid):
                    print(f"ELIGIBILITY RESULT: MEMBER STATUS INACTIVE")
                    return {
                        "status": "MEMBER_INACTIVE",
                        "message": text,
                        "flag": 0,
                        "success": False,
                        "background_color": bg_color,
                        "background_hex": bg_hex,
                        "detection_method": "Text-based" if text_indicates_inactive else "Color-based",
                        "location": location
                    }
                elif text_indicates_invalid or color_indicates_invalid:
                    print(f"ELIGIBILITY RESULT: INVALID/ERROR")
                    return {
                        "status": "INVALID",
                        "message": text,
                        "flag": -1,
                        "success": False,
                        "background_color": bg_color,
                        "background_hex": bg_hex,
                        "detection_method": "Text-based" if text_indicates_invalid else "Color-based",
                        "location": location
                    }
            
            # If we found elements but couldn't classify them
            if element_data:
                first_element = element_data[0]
                print("ELIGIBILITY RESULT: UNKNOWN STATUS")
                return {
                    "status": "UNKNOWN",
                    "message": first_element['text'],
                    "flag": -1,  # Default to invalid for unknown
                    "success": None,
                    "background_color": first_element['backgroundColor'],
                    "background_hex": self.rgb_to_hex(first_element['backgroundColor']),
                    "detection_method": "Unknown classification",
                    "location": first_element['location']
                }
            
            # No elements found
            print("ELIGIBILITY RESULT: No eligibility response detected")
            return {
                "status": "NO_RESPONSE",
                "message": "No eligibility response detected",
                "flag": -1,
                "success": None,
                "background_color": None,
                "background_hex": None,
                "detection_method": "No detection",
                "location": "none"
            }
            
        except Exception as e:
            print(f"Error checking eligibility response: {str(e)}")
            return {
                "status": "ERROR",
                "message": f"Error checking response: {str(e)}",
                "flag": -1,
                "success": False,
                "background_color": None,
                "background_hex": None,
                "detection_method": "Error",
                "location": "error"
            }

    def process_patient(self, patient_data):
        """Process patient using member_id for eligibility check but return both IDs for Flask route"""
        provider_name = patient_data.get("provider_name", "")
        member_id = str(patient_data.get("member_id", ""))  # For eligibility check (insurance member ID)
        patient_id = str(patient_data.get("auth_id", ""))   # For database logging (auth_id as patient_id)
        dob = patient_data.get("patient_dob", "")
        payer_name = patient_data.get("payer", "")
        service_type = "Health Benefit Plan Coverage"
        
        print(f"Processing Member ID: {member_id} for Patient ID: {patient_id}")
        
        if not self.fill_payer(payer_name):
            return {"success": False, "error": "Failed to fill payer information"}
            
        if not self.fill_provider(provider_name):
            return {"success": False, "error": "Failed to fill provider information"}
            
        if not self.fill_patient_data(member_id, dob):  # Use member_id for eligibility
            return {"success": False, "error": "Failed to fill patient data"}
            
        if not self.fill_service_type_and_submit(service_type):
            return {"success": False, "error": "Failed to fill service type and submit"}
        
        # Check eligibility response
        eligibility_result = self.check_eligibility_response()
        
        print(f"SUCCESS: Member ID {member_id} processed for Patient ID {patient_id}!")
        print(f"Eligibility Status: {eligibility_result['status']}")
        print(f"Flag: {eligibility_result['flag']}")
        print(f"Message: {eligibility_result['message']}")
        
        # Return exactly what Flask route expects
        final_result = {
            "success": True,
            "patient_id": str(patient_data.get("patient_id")),  # Flask route expects this for database logging
            "member_id": member_id,         # For eligibility check (insurance member ID)
            "auth_id": patient_data.get("auth_id"),
            "procedure_code": patient_data.get("procedure_code"),
            "diagnosis_code": patient_data.get("diagnosis_code"),
            "from_date": patient_data.get("from_date"),
            "first_name": patient_data.get("first_name"),
            "last_name": patient_data.get("last_name"),
            "gender": patient_data.get("gender"),
            "patient_dob": patient_data.get("patient_dob"),
            "payer": patient_data.get("payer"),
            "provider_name": patient_data.get("provider_name"),
            "provider_npi_id": patient_data.get("provider_npi_id"),
            "eligibility_result": eligibility_result
        }
        
        return final_result

    def run(self, input_data):
        try:
            self.setup_driver()
            self.login_to_availity()
            self.navigate_to_eligibility()
            
            result = self.process_patient(input_data)
            return result
                
        except Exception as e:
            print(f"Error: {str(e)}")
            return {"success": False, "error": str(e)}
        finally:
            if self.driver:
                self.driver.quit()

def main():
    if len(sys.argv) < 2:
        print("No input JSON provided")
        sys.exit(1)
    
    try:
        input_data = json.loads(sys.argv[1])
        
        # Required fields - Flask route expects both member_id and auth_id (as patient_id)
        required_fields = ['provider_name', 'member_id', 'patient_dob', 'payer', 'auth_id']
        missing_fields = [field for field in required_fields if not input_data.get(field)]
        
        if missing_fields:
            print(f"Missing required fields: {missing_fields}")
            sys.exit(1)
        
        bot = EligibilityBot()
        result = bot.run(input_data)
        
        # Print final result exactly as Flask route expects
        print("FINAL_RESULT:", json.dumps(result))
        
        sys.exit(0 if result.get("success") else 1)
        
    except Exception as e:
        print(f"Failed to parse input JSON: {str(e)}")
        sys.exit(1)

if __name__ == "__main__":
    main()
