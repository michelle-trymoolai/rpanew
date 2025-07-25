import time
import mysql.connector
from selenium import webdriver
from selenium.webdriver.common.by import By
from selenium.webdriver.chrome.service import Service
from selenium.webdriver.chrome.options import Options
from selenium.webdriver.support.ui import WebDriverWait
from selenium.webdriver.support import expected_conditions as EC
from selenium.common.exceptions import TimeoutException, WebDriverException
import os
import platform
from pathlib import Path
import logging
from dotenv import load_dotenv
from urllib.parse import urlparse
import subprocess
import sys

load_dotenv()

logger = logging.getLogger(__name__)

def parse_sqlalchemy_uri(uri):
    parsed = urlparse(uri)
    return {
        'host': parsed.hostname,
        'port': parsed.port or 3306,
        'user': parsed.username,
        'password': parsed.password,
        'database': parsed.path[1:]
    }

SQLALCHEMY_URI = os.getenv('SQLALCHEMY_DATABASE_URI')
DB_CONFIG = parse_sqlalchemy_uri(SQLALCHEMY_URI) if SQLALCHEMY_URI else None

def install_chromedriver_manager():
    """Install webdriver-manager if not already installed"""
    try:
        import webdriver_manager
    except ImportError:
        print("Installing webdriver-manager...")
        subprocess.check_call([sys.executable, "-m", "pip", "install", "webdriver-manager"])

def setup_chrome_driver():
    """Setup Chrome driver with automatic version management"""
    try:
        # Try using webdriver-manager for automatic ChromeDriver management
        install_chromedriver_manager()
        from webdriver_manager.chrome import ChromeDriverManager
        
        options = Options()
        options.add_argument("--start-maximized")
        options.add_argument("--headless")
        options.add_argument("--no-sandbox")
        options.add_argument("--disable-dev-shm-usage")
        options.add_argument("--disable-gpu")
        options.add_argument("--window-size=1920,1080")
        
        # Use ChromeDriverManager to automatically download compatible version
        service = Service(ChromeDriverManager().install())
        driver = webdriver.Chrome(service=service, options=options)
        return driver
        
    except Exception as e:
        print(f"Failed to setup Chrome driver with webdriver-manager: {e}")
        
        # Fallback to manual ChromeDriver path
        try:
            base_dir = Path(__file__).resolve().parent
            if platform.system() == 'Windows':
                chromedriver_path = base_dir / 'chromedriver.exe'
            else:
                chromedriver_path = base_dir / 'chromedriver'
            
            if not chromedriver_path.exists():
                raise FileNotFoundError(f"ChromeDriver not found at {chromedriver_path}")
            
            # Make sure it's executable
            if platform.system() != 'Windows':
                os.chmod(chromedriver_path, 0o755)
            
            options = Options()
            options.add_argument("--start-maximized")
            options.add_argument("--headless")
            options.add_argument("--no-sandbox")
            options.add_argument("--disable-dev-shm-usage")
            options.add_argument("--disable-gpu")
            
            service = Service(str(chromedriver_path))
            driver = webdriver.Chrome(service=service, options=options)
            return driver
            
        except Exception as fallback_error:
            print(f"Fallback ChromeDriver setup also failed: {fallback_error}")
            raise

def get_provider_id_by_name(first_name, last_name, auth_id):
    """
    Function to get provider NPI ID by searching with first and last name
    Returns the provider_id from the database after updating with NPI info
    """
    if not DB_CONFIG:
        print("Database configuration not found. Please check SQLALCHEMY_DATABASE_URI environment variable.")
        return None
    
    driver = None
    provider_id = None
    
    print(f"Starting NPI search for: {first_name} {last_name}")

    try:
        # Setup Chrome driver with automatic version management
        driver = setup_chrome_driver()
        wait = WebDriverWait(driver, 15)
        
        print(f"Searching NPI for: {first_name} {last_name}")
        driver.get("https://npiregistry.cms.hhs.gov/search")
        
        # Wait for page to load completely
        time.sleep(2)

        # Fill search fields with better error handling
        first_name_field = wait.until(EC.presence_of_element_located((By.ID, "firstName")))
        first_name_field.clear()
        first_name_field.send_keys(first_name)
        
        last_name_field = driver.find_element(By.ID, "lastName")
        last_name_field.clear()
        last_name_field.send_keys(last_name)

        # Click Search button with improved reliability
        search_btn = wait.until(EC.element_to_be_clickable((By.NAME, "search")))
        driver.execute_script("arguments[0].scrollIntoView({block: 'center'});", search_btn)
        time.sleep(1)
        
        try:
            search_btn.click()
        except Exception:
            print("Regular click failed, trying JavaScript click")
            driver.execute_script("arguments[0].click();", search_btn)

        # Wait for results with better timeout handling
        try:
            npi_button = wait.until(EC.presence_of_element_located((
                By.XPATH, "//table[contains(@class, 'table-hover')]//button[@class='btn btn-link']"
            )))
            npi_number = npi_button.text.strip()
            print(f"Found NPI: {npi_number}")

            # Click the NPI button to open details
            driver.execute_script("arguments[0].click();", npi_button)
            time.sleep(3)

            # Extract additional provider details
            provider_name = extract_provider_name(driver)
            provider_name = extract_provider_name(driver)

            # Protect against junk values
            if provider_name and provider_name.lower().startswith("provider information for"):
                print("⚠️ Skipping provider_name update due to invalid format.")
                provider_name = None  # Treat as missing

            
            # Extract status from detail page
            page_text = driver.page_source.lower()
            if "active" in page_text:
                status = "Active"
            elif "inactive" in page_text:
                status = "Inactive"
            else:
                status = "Unknown"

            print(f"Provider Name: {provider_name}, Status: {status}")
           
            # Update database with the found NPI and details
            provider_id = update_provider_in_db(first_name, last_name, npi_number, provider_name)

            # Update prescrubbing table if auth_id is provided
            if auth_id and provider_id:
                update_npi_validation_status(auth_id, provider_id, "PASS")
            
            if provider_id:
                print(f"Successfully updated database with provider_id: {provider_id}")
            else:
                print("Failed to update database")
                
        except TimeoutException:
            print(f"No results found for: {first_name} {last_name}")
            if auth_id:
                update_npi_validation_status(auth_id, None, "FAIL")

    except WebDriverException as e:
        print(f"WebDriver error for {first_name} {last_name}: {e}")
        if "This version of ChromeDriver only supports Chrome version" in str(e):
            print("\n🔧 SOLUTION: ChromeDriver version mismatch detected!")
            print("Run: pip install webdriver-manager")
            print("This will automatically manage ChromeDriver versions.")
        provider_id = None

    except Exception as e:
        print(f"Unexpected error for {first_name} {last_name}: {e}")
        provider_id = None
    
    finally:
        if driver:
            try:
                driver.quit()
                print("WebDriver closed successfully")
            except Exception as e:
                print(f"Error closing WebDriver: {e}")
    
    return provider_id

def extract_provider_name(driver):
    """Extract provider name from the NPI detail page with improved selectors"""
    try:
        selectors = [
            "//h2[contains(@class, 'provider-name')]",
            "//div[contains(@class, 'provider-name')]", 
            "//span[contains(text(), 'Provider Name')]/following-sibling::*",
            "//td[contains(text(), 'Provider Name')]/following-sibling::td",
            "//div[@class='row']//strong[contains(text(), 'Name')]/parent::*/following-sibling::*",
            "//h1", "//h2", "//h3"  # Fallback to any heading
        ]
        
        for selector in selectors:
            try:
                elements = driver.find_elements(By.XPATH, selector)
                for element in elements:
                    text = element.text.strip()
                    if text and len(text) > 2 and not text.isdigit():
                        return text
            except Exception:
                continue
        
        return None
    except Exception as e:
        print(f"Error extracting provider name: {e}")
        return None

def update_provider_in_db(first_name, last_name, npi_number, provider_name):
    """Update the provider_details table with NPI number and other details"""
    connection = None
    cursor = None

    try:
        connection = mysql.connector.connect(**DB_CONFIG)
        cursor = connection.cursor()

        # Check if record exists
        check_query = """
        SELECT provider_id FROM provider_details 
        WHERE first_name = %s AND last_name = %s
        """
        cursor.execute(check_query, (first_name, last_name))
        result = cursor.fetchone()

        if result:
            provider_id = result[0]
            if provider_name:
                update_query = """
                UPDATE provider_details 
                SET npi_number = %s, provider_name = %s
                WHERE provider_id = %s
                """
                cursor.execute(update_query, (npi_number, provider_name, provider_id))
            else:
                update_query = """
                UPDATE provider_details 
                SET npi_number = %s
                WHERE provider_id = %s
                """
                cursor.execute(update_query, (npi_number, provider_id))

            connection.commit()
            print(f"Updated existing record for {first_name} {last_name} with provider_id: {provider_id}")
            return provider_id

        else:
            insert_query = """
            INSERT INTO provider_details (first_name, last_name, npi_number, provider_name)
            VALUES (%s, %s, %s, %s)
            """
            cursor.execute(insert_query, (first_name, last_name, npi_number, provider_name))
            provider_id = cursor.lastrowid
            connection.commit()
            print(f"Inserted new record for {first_name} {last_name} with provider_id: {provider_id}")
            return provider_id

    except mysql.connector.Error as error:
        print(f"❌ Database error: {error}")
        if connection:
            connection.rollback()
        return None

    except Exception as e:
        print(f"❌ Unexpected error in database operation: {e}")
        if connection:
            connection.rollback()
        return None

    finally:
        if cursor:
            cursor.close()
        if connection and connection.is_connected():
            connection.close()


def update_npi_validation_status(auth_id, provider_id, status):
    """Update NPI validation status in prescrubbing table"""
    connection = None
    cursor = None

    try:
        connection = mysql.connector.connect(**DB_CONFIG)
        cursor = connection.cursor()

        # Update prescrubbing row for this auth_id
        update_query = """
        UPDATE prescrubbing 
        SET npi_validation_status = %s 
        WHERE auth_id = %s
        """
        cursor.execute(update_query, (status, auth_id))
        
        if cursor.rowcount == 0:
            print(f"⚠️ No prescrubbing rows updated for auth_id: {auth_id}")
        else:
            print(f"✅ Updated prescrubbing table for auth_id {auth_id} with status '{status}'")

        # If provider_id exists, update all other prescrubbing records linked by patient_id
        if provider_id:
            update_query_patients = """
            UPDATE prescrubbing 
            SET npi_validation_status = %s 
            WHERE patient_id IN (
                SELECT pd.patient_id 
                FROM patient_details pd
                WHERE pd.provider_id = %s
            )
            """
            cursor.execute(update_query_patients, (status, provider_id))
            print(f"✅ Updated related patient records for provider_id {provider_id} to '{status}'")

        connection.commit()

    except mysql.connector.Error as error:
        print(f"❌ Database error while updating NPI status: {error}")

    finally:
        if cursor:
            cursor.close()
        if connection and connection.is_connected():
            connection.close()

# Test function
def test_npi_lookup():
    """Test the NPI lookup functionality"""
    test_cases = [
        ("John", "Smith", "TEST-001"),
        ("Jane", "Doe", "TEST-002")
    ]
    
    for first_name, last_name, auth_id in test_cases:
        print(f"\n--- Testing: {first_name} {last_name} ---")
        result = get_provider_id_by_name(first_name, last_name, auth_id)
        print(f"Result: {result}")

if __name__ == "__main__":
    # Example usage
    test_npi_lookup()
