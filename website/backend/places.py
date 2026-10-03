"""
GigPilot - busy places dataset for Hyderabad.

Well-known spots where delivery orders concentrate: malls, food streets,
markets, office parks and transit hubs. Coordinates are approximate, and
`busy` (1 = quiet, 5 = one of the busiest in the city) is an editorial
estimate, not measured sales - partner order data would replace it.

Each zone's baseline popularity is derived from the places inside it, and
any place can be switched on as the city's current "busy place" to create
a demand surge there (see World.set_busy_place).
"""

# (id, name, lat, lon, category, busy)
_PLACE_ROWS = [
    ("charminar", "Charminar", 17.3616, 78.4747, "market", 5),
    ("laad-bazaar", "Laad Bazaar", 17.3608, 78.4730, "market", 4),
    ("paradise", "Paradise Biryani, Secunderabad", 17.4435, 78.4867, "food", 5),
    ("sec-station", "Secunderabad Railway Station", 17.4337, 78.5016, "transit", 5),
    ("inorbit", "Inorbit Mall, Madhapur", 17.4345, 78.3866, "mall", 5),
    ("ikea", "IKEA, HITEC City", 17.4414, 78.3772, "mall", 4),
    ("mindspace", "Mindspace IT Park", 17.4411, 78.3810, "office", 5),
    ("dlf", "DLF Cyber City, Gachibowli", 17.4474, 78.3520, "office", 4),
    ("sarath-city", "Sarath City Capital Mall, Kondapur", 17.4578, 78.3635, "mall", 5),
    ("wipro-circle", "Wipro Circle, Financial District", 17.4256, 78.3408, "office", 4),
    ("nexus-kukatpally", "Nexus Mall, Kukatpally", 17.4847, 78.3890, "mall", 5),
    ("kphb", "KPHB Colony food street", 17.4933, 78.3915, "food", 4),
    ("miyapur-metro", "Miyapur Metro Station", 17.4965, 78.3730, "transit", 3),
    ("jubilee-checkpost", "Jubilee Hills Check Post", 17.4313, 78.4104, "nightlife", 5),
    ("film-nagar", "Film Nagar", 17.4140, 78.4090, "nightlife", 3),
    ("gvk-one", "GVK One Mall, Banjara Hills", 17.4196, 78.4483, "mall", 4),
    ("banjara-road-12", "Banjara Hills Road No. 12", 17.4100, 78.4360, "food", 4),
    ("ameerpet-metro", "Ameerpet Metro Station", 17.4374, 78.4487, "transit", 5),
    ("begumpet", "Begumpet shopping stretch", 17.4440, 78.4620, "mall", 3),
    ("punjagutta", "Punjagutta Circle", 17.4270, 78.4510, "mall", 4),
    ("tank-bund", "Tank Bund and Necklace Road", 17.4239, 78.4738, "food", 3),
    ("abids", "Abids GPO", 17.3920, 78.4760, "market", 4),
    ("koti", "Koti Sultan Bazaar", 17.3850, 78.4840, "market", 4),
    ("himayatnagar-road", "Himayatnagar Main Road", 17.4010, 78.4870, "food", 4),
    ("rtc-x-roads", "RTC X Roads", 17.4063, 78.4975, "nightlife", 4),
    ("dilsukhnagar", "Dilsukhnagar Bus Stand", 17.3688, 78.5260, "market", 5),
    ("lb-nagar", "LB Nagar Circle", 17.3480, 78.5510, "transit", 4),
    ("uppal-ring-road", "Uppal Ring Road", 17.4010, 78.5590, "transit", 3),
    ("tarnaka", "Tarnaka and Osmania University", 17.4140, 78.5290, "food", 3),
    ("malkajgiri", "Malkajgiri Main Road", 17.4500, 78.5300, "food", 2),
    ("as-rao-nagar", "AS Rao Nagar Main Road", 17.4790, 78.5560, "food", 3),
    ("alwal", "Alwal Main Road", 17.5020, 78.5090, "food", 2),
    ("bowenpally-market", "Bowenpally Market", 17.4680, 78.4770, "market", 3),
    ("kompally", "Kompally multiplex stretch", 17.5350, 78.4830, "mall", 3),
    ("medchal-bus-stand", "Medchal Bus Stand", 17.6297, 78.4814, "market", 2),
    ("manikonda", "Manikonda Main Road", 17.4050, 78.3880, "food", 3),
    ("mehdipatnam", "Mehdipatnam Rythu Bazaar", 17.3950, 78.4410, "market", 4),
    ("tolichowki", "Tolichowki food street", 17.4010, 78.4130, "food", 5),
    ("attapur", "Attapur Pillar 143", 17.3690, 78.4290, "food", 3),
    ("nizampet", "Nizampet X Roads", 17.5180, 78.3830, "food", 3),
    ("airport", "Rajiv Gandhi International Airport", 17.2403, 78.4294, "transit", 4),
]

# which demand shape (see data._DEMAND_BUMPS) each kind of place follows
CATEGORY_PROFILE = {
    "mall": "nightlife",
    "nightlife": "nightlife",
    "food": "residential",
    "office": "tech",
    "transit": "commercial",
    "market": "commercial",
}

PLACES = [
    {"id": pid, "name": name, "lat": lat, "lon": lon, "category": category, "busy": busy}
    for pid, name, lat, lon, category, busy in _PLACE_ROWS
]
