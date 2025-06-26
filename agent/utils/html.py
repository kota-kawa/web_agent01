import re

def strip_html(ht: str) -> str:
    """Remove style/script tags to reduce text size"""
    ht = re.sub(r"<style.*?>.*?</style>", "", ht, flags=re.S | re.I)
    ht = re.sub(r"<script.*?>.*?</script>", "", ht, flags=re.S | re.I)
    return ht.strip()
