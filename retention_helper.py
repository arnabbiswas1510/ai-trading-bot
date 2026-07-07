import re

def increment_retention(current: str) -> str:
    if not current:
        return "1d"
    
    # Parse current
    days = 0
    
    # Check for months (assuming 1m = 30d for simplicity, though 'm' implies 4w roughly)
    # Actually, 1m = 4w. 1w = 5d? Market days or calendar days?
    # Usually "1d", "1w", "1m" are standard. Let's use calendar days: 1w = 7d, 1m = 30d.
    m_match = re.search(r'(\d+)m', current)
    w_match = re.search(r'(\d+)w', current)
    d_match = re.search(r'(\d+)d', current)
    
    if m_match: days += int(m_match.group(1)) * 30
    if w_match: days += int(w_match.group(1)) * 7
    if d_match: days += int(d_match.group(1))
    
    days += 1
    
    # Format back
    res = []
    m = days // 30
    rem = days % 30
    w = rem // 7
    d = rem % 7
    
    if m > 0: res.append(f"{m}m")
    if w > 0: res.append(f"{w}w")
    if d > 0: res.append(f"{d}d")
    
    return " ".join(res) if res else "1d"
