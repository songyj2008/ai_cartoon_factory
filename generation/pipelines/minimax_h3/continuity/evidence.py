"""Recover exact source spans when a model changes whitespace only."""
def source_quote(text, quote):
    if not isinstance(text,str) or not isinstance(quote,str) or not quote.strip(): return None
    if quote in text:return quote
    indices=[i for i,c in enumerate(text) if not c.isspace()]
    compact=''.join(text[i] for i in indices)
    needle=''.join(c for c in quote if not c.isspace())
    if not needle:return None
    start=compact.find(needle)
    if start<0:return None
    # Repeated identical source text is still valid evidence. Reject only when
    # whitespace normalization would choose between different original strings.
    recovered=text[indices[start]:indices[start+len(needle)-1]+1]
    following=compact.find(needle,start+1)
    while following>=0:
        candidate=text[indices[following]:indices[following+len(needle)-1]+1]
        if candidate!=recovered:return None
        following=compact.find(needle,following+1)
    return recovered
