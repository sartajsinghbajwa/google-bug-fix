"""Offline fallbacks for invitations saved with removed Google Font options.

These IDs are accepted only for backwards compatibility. They never load Google.
"""
LEGACY_FONT_FALLBACKS = {
    'google:Playfair Display': 'serif',
    'google:Great Vibes': 'script',
    'google:Montserrat': 'sans',
    'google:Poppins': 'sans',
    'google:Lora': 'serif',
    'google:Dancing Script': 'script',
    'google:Pacifico': 'script',
    'google:Cormorant Garamond': 'serif',
    'google:Libre Baskerville': 'serif',
    'google:DM Sans': 'sans',
}

def offline_font(value):
    return LEGACY_FONT_FALLBACKS.get(value, value)
