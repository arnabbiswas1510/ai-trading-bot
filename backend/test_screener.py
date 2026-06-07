import sys
import os
import json

# Add current dir to path to import
sys.path.append(os.path.dirname(os.path.abspath(__file__)))

import database as db
import screener

def test_screener():
    print("Initializing Database...")
    db.init_db()
    
    # Overwrite watchlist for testing to speed it up
    db.set_setting("watchlist", "AAPL")
    
    print("\nWatchlist is:", db.get_watchlist())
    
    print("\nFetching market direction...")
    m = screener.get_market_direction()
    print("Market Direction Status:", m["status"])
    print("Market Score:", m["score"])
    print("Index stats:", m["indices"])
    
    print("\nRunning scan on AAPL...")
    results = screener.run_canslim_screener()
    
    if results:
        print("\nScan Results successful!")
        for r in results:
            print(f"\nTicker: {r['ticker']}")
            print(f"Total Score: {r['total_score']}/100")
            print(f"C Score: {r['score_c']}/15")
            print(f"A Score: {r['score_a']}/15")
            print(f"N Score: {r['score_n']}/15")
            print(f"S Score: {r['score_s']}/15")
            print(f"L Score: {r['score_l']}/15")
            print(f"I Score: {r['score_i']}/10")
            print(f"M Score: {r['score_m']}/15")
            print("Details:", json.dumps(r['details'], indent=2))
            
        print("\nCaching results to DB...")
        db.save_screener_results(results)
        
        cached = db.get_screener_results()
        print(f"Cached results retrieved: {len(cached)} records found.")
    else:
        print("\nScan failed or returned empty results.")

if __name__ == "__main__":
    test_screener()
