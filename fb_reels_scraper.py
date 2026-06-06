import csv
import datetime
import random
import re
import time
from pathlib import Path

from selenium import webdriver
from selenium.common.exceptions import NoSuchElementException, TimeoutException, WebDriverException
from selenium.webdriver.chrome.options import Options
from selenium.webdriver.chrome.service import Service
from selenium.webdriver.common.by import By
from selenium.webdriver.support import expected_conditions as EC
from selenium.webdriver.support.ui import WebDriverWait
from webdriver_manager.chrome import ChromeDriverManager

BASE_DIR = Path(__file__).resolve().parent
UIDS_FILE = BASE_DIR / "uids.txt"
REPORT_FILE = BASE_DIR / "reels_views_report.csv"
CSV_HEADERS = ["UID", "Reel Link", "Old View", "New View", "Difference (Growth)", "Scraped At"]


def log(message: str) -> None:
    print(message)


def load_historical_views() -> dict[str, str]:
    if not REPORT_FILE.exists():
        return {}

    historical_views: dict[str, str] = {}
    with REPORT_FILE.open("r", encoding="utf-8-sig", newline="") as csvfile:
        reader = csv.DictReader(csvfile)
        if not reader.fieldnames:
            return {}

        count_field = "New View" if "New View" in reader.fieldnames else "View Count"
        for row in reader:
            link = (row.get("Reel Link") or "").strip()
            if not link:
                continue
            value = (row.get(count_field) or "").strip()
            if value:
                historical_views[link] = value

    log(f"[THÔNG BÁO] Tải lịch sử thành công: {len(historical_views)} reels.")
    return historical_views


def parse_view_count_to_int(value: str) -> int | None:
    if not value:
        return None

    normalized = value.upper().strip()
    normalized = normalized.replace("LƯỢT XEM", "")
    normalized = normalized.replace("VIEWS", "")
    normalized = normalized.replace(" ", "")
    normalized = normalized.replace(",", ".")

    match = re.match(r"^(\d+(?:\.\d+)?)([KM]?)$", normalized)
    if not match:
        return None

    number = float(match.group(1))
    suffix = match.group(2)
    if suffix == "K":
        return int(number * 1_000)
    if suffix == "M":
        return int(number * 1_000_000)
    return int(number)


def format_difference(old_value: str, new_value: str) -> str:
    old_int = parse_view_count_to_int(old_value)
    new_int = parse_view_count_to_int(new_value)
    if old_int is None:
        return "New Reel"
    if new_int is None:
        return "N/A"

    difference = new_int - old_int
    return f"+{difference}" if difference >= 0 else str(difference)


def normalize_report_file() -> None:
    if not REPORT_FILE.exists() or REPORT_FILE.stat().st_size == 0:
        return

    with REPORT_FILE.open("r", encoding="utf-8-sig", newline="") as csvfile:
        reader = csv.DictReader(csvfile)
        if not reader.fieldnames:
            return
        if reader.fieldnames == CSV_HEADERS:
            return

        normalized_rows = []
        for row in reader:
            if not row.get("Reel Link"):
                continue
            if "New View" in reader.fieldnames and "Old View" in reader.fieldnames:
                normalized_rows.append({field: row.get(field, "") for field in CSV_HEADERS})
            else:
                normalized_rows.append({
                    "UID": row.get("UID", ""),
                    "Reel Link": row.get("Reel Link", ""),
                    "Old View": row.get("View Count", ""),
                    "New View": row.get("View Count", ""),
                    "Difference (Growth)": "0",
                    "Scraped At": row.get("Scraped At", ""),
                })

    with REPORT_FILE.open("w", encoding="utf-8-sig", newline="") as csvfile:
        writer = csv.DictWriter(csvfile, fieldnames=CSV_HEADERS)
        writer.writeheader()
        writer.writerows(normalized_rows)

    log(f"[THÔNG BÁO] Chuyển đổi CSV sang định dạng mới với {len(normalized_rows)} dòng.")


def load_uids() -> list[str]:
    if not UIDS_FILE.exists():
        log(f"[CẢNH BÁO] Không tìm thấy tệp UID: {UIDS_FILE}")
        return []

    uids = []
    with UIDS_FILE.open("r", encoding="utf-8") as fh:
        for raw_line in fh:
            line = raw_line.strip()
            if not line or line.startswith("#"):
                continue
            uids.append(line)
    return uids


def create_driver() -> webdriver.Chrome:
    options = Options()
    options.add_argument("--disable-blink-features=AutomationControlled")
    options.add_argument("--start-maximized")
    options.add_argument(
        "--user-agent=Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/125.0.0.0 Safari/537.36"
    )

    width = random.randint(1200, 1366)
    height = random.randint(800, 950)
    options.add_argument(f"--window-size={width},{height}")

    # Nếu bạn muốn sử dụng profile Chrome hiện tại để đăng nhập Facebook tự động,
    # hãy bỏ comment hai dòng dưới và thay đường dẫn sau bằng đường dẫn profile thực tế của bạn.
    # Đường dẫn thông thường trên Windows là:
    # C:\Users\<Tên Người Dùng>\AppData\Local\Google\Chrome\User Data
    # Trên Mac/Linux thường là:
    # /Users/<Tên Người Dùng>/Library/Application Support/Google/Chrome
    # hoặc /home/<Tên Người Dùng>/.config/google-chrome
    # options.add_argument(r"--user-data-dir=C:\Users\USERNAME\AppData\Local\Google\Chrome\User Data")
    # options.add_argument("--profile-directory=Default")

    service = Service(ChromeDriverManager().install())
    driver = webdriver.Chrome(service=service, options=options)
    driver.set_page_load_timeout(30)
    return driver


def clean_view_count(raw_text: str) -> str:
    raw_value = raw_text.replace("\n", " ").replace(",", ".").strip()
    raw_value = re.sub(r"\s+", " ", raw_value)

    match = re.search(r"(\d+(?:[\.,]\d+)?\s*[KMkm]?)", raw_value)
    if match:
        cleaned = match.group(1).upper().replace(" ", "")
        return cleaned
    return "N/A or Hidden"


def extract_view_count(anchor) -> str:
    candidate_texts = []
    if anchor.text:
        candidate_texts.append(anchor.text)

    try:
        parent = anchor.find_element(By.XPATH, "./ancestor::div[1]")
        for candidate in parent.find_elements(By.XPATH, ".//span | .//div"):
            if candidate.text:
                candidate_texts.append(candidate.text)
    except NoSuchElementException:
        pass

    for text in candidate_texts:
        cleaned = clean_view_count(text)
        if cleaned != "N/A or Hidden":
            return cleaned

    return "N/A or Hidden"


def wait_for_reels(driver: webdriver.Chrome) -> bool:
    try:
        WebDriverWait(driver, 10).until(
            EC.presence_of_element_located((By.XPATH, "//a[contains(@href, '/reel/')]") )
        )
        return True
    except TimeoutException:
        return False


def smooth_scroll(driver: webdriver.Chrome) -> None:
    last_height = driver.execute_script("return document.body.scrollHeight")
    for scroll_round in range(5):
        current = 0
        while current < last_height:
            current += 400
            driver.execute_script("window.scrollTo(0, arguments[0]);", current)
            time.sleep(random.uniform(0.2, 0.5))
        time.sleep(random.uniform(1.0, 1.4))
        new_height = driver.execute_script("return document.body.scrollHeight")
        if new_height == last_height:
            break
        last_height = new_height


def append_to_csv(rows: list[tuple[str, str, str, str, str, str]]) -> None:
    write_header = not REPORT_FILE.exists() or REPORT_FILE.stat().st_size == 0
    with REPORT_FILE.open("a", encoding="utf-8-sig", newline="") as csvfile:
        writer = csv.writer(csvfile)
        if write_header:
            writer.writerow(CSV_HEADERS)
        writer.writerows(rows)


def extract_reels(driver: webdriver.Chrome, uid: str, seen_links: set[str], historical_views: dict[str, str]) -> list[tuple[str, str, str, str, str, str]]:
    url = f"https://www.facebook.com/{uid}/reels/"
    log(f"[ĐANG QUÉT] Page UID: {uid} - Truy cập {url}")
    driver.get(url)

    if not wait_for_reels(driver):
        log(f"[CẢNH BÁO] Page layout not loaded or restricted cho UID: {uid}")
        return []

    smooth_scroll(driver)
    time.sleep(random.uniform(2.5, 4.0))

    anchors = driver.find_elements(By.XPATH, "//a[contains(@href, '/reel/')]")
    results: list[tuple[str, str, str, str, str, str]] = []
    scraped_at = datetime.datetime.utcnow().isoformat()

    for anchor in anchors:
        try:
            reel_link = anchor.get_attribute("href") or ""
            if not reel_link or reel_link in seen_links:
                continue

            new_view = extract_view_count(anchor)
            old_view = historical_views.get(reel_link, "N/A")
            difference = format_difference(old_view, new_view)
            seen_links.add(reel_link)

            log(
                f"      + Link: {reel_link} | Cũ: {old_view} | Mới: {new_view} | Tăng trưởng: {difference}"
            )

            results.append((uid, reel_link, old_view, new_view, difference, scraped_at))
        except WebDriverException as exc:
            log(f"[CẢNH BÁO] Không thể trích xuất reel cho UID {uid}: {exc}")
            continue

    return results


def main() -> None:
    log("[KHỞI ĐỘNG] Bắt đầu quét Facebook Reels...")
    uids = load_uids()
    if not uids:
        log("[CẢNH BÁO] Không tìm thấy UID trong uids.txt. Vui lòng thêm UID và thử lại.")
        return

    normalize_report_file()
    historical_views = load_historical_views()
    driver = create_driver()
    seen_links: set[str] = set()

    try:
        for uid in uids:
            try:
                rows = extract_reels(driver, uid, seen_links, historical_views)
                if rows:
                    append_to_csv(rows)
                    log(f"[THÀNH CÔNG] Tìm thấy {len(rows)} video, đã lưu vào CSV.")
                else:
                    log(f"[CẢNH BÁO] Không tìm thấy dữ liệu hoặc bị chặn ở UID: {uid}")
            except TimeoutException:
                log(f"[CẢNH BÁO] Timeout khi quét UID: {uid}")
            except WebDriverException as exc:
                log(f"[CẢNH BÁO] Lỗi Selenium UID {uid}: {exc}")
            except Exception as exc:
                log(f"[CẢNH BÁO] Lỗi không xác định UID {uid}: {exc}")
            finally:
                delay = random.randint(5, 12)
                log(f"[TẠM DỪNG] Đợi {delay} giây trước UID tiếp theo...")
                time.sleep(delay)
    finally:
        driver.quit()
        log("[KẾT THÚC] Đã đóng trình duyệt.")

    log("[HOÀN THÀNH] Quét Facebook Reels hoàn tất.")


if __name__ == "__main__":
    main()
