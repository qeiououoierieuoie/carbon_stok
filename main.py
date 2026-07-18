from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import HTMLResponse
import ee
import datetime

app = FastAPI()

# ======================
# CORS
# ======================
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# ======================
# INIT GEE
# ======================
ee.Initialize(project='imposing-kayak-470402-v4')

# ======================
# AOI PADANG
# ======================
aoi = ee.Geometry.Point([100.3624642, -0.9242544]).buffer(5000)

# ======================
# CLOUD MASK
# ======================
def mask_s2(image):
    scl = image.select('SCL')
    cloud_mask = (
        scl.neq(3)
        .And(scl.neq(8))
        .And(scl.neq(9))
        .And(scl.neq(10))
        .And(scl.neq(11))
    )
    return image.updateMask(cloud_mask).divide(10000)


# ======================
# BASE CARBON MODEL
# ======================
def compute_carbon(img):
    ndvi = img.normalizedDifference(['B8', 'B4']).rename('NDVI')
    biomass = ndvi.multiply(250).add(20)
    carbon = biomass.multiply(0.47).max(0).rename("carbon")
    return carbon


# ======================
# GEE FETCHERS
# ======================
def get_carbon_window(start_day, end_day):
    today = ee.Date(datetime.datetime.utcnow())
    start_date = today.advance(-end_day, 'day')
    end_date = today.advance(-start_day, 'day')

    col = (
        ee.ImageCollection("COPERNICUS/S2_SR_HARMONIZED")
        .filterBounds(aoi)
        .filterDate(start_date, end_date)
        .filter(ee.Filter.lt("CLOUDY_PIXEL_PERCENTAGE", 80))
        .map(mask_s2)
    )
    if col.size().getInfo() == 0: 
        return None
    return compute_carbon(col.median())


def get_carbon_custom_range(start_str: str, end_str: str):
    col = (
        ee.ImageCollection("COPERNICUS/S2_SR_HARMONIZED")
        .filterBounds(aoi)
        .filterDate(ee.Date(start_str), ee.Date(end_str))
        .filter(ee.Filter.lt("CLOUDY_PIXEL_PERCENTAGE", 80))
        .map(mask_s2)
    )
    if col.size().getInfo() == 0: 
        return None
    return compute_carbon(col.median())


def get_carbon_month(year, month):
    start_date = ee.Date.fromYMD(year, month, 1)
    end_date = start_date.advance(1, 'month')

    col = (
        ee.ImageCollection("COPERNICUS/S2_SR_HARMONIZED")
        .filterBounds(aoi)
        .filterDate(start_date, end_date)
        .filter(ee.Filter.lt("CLOUDY_PIXEL_PERCENTAGE", 80))
        .map(mask_s2)
    )
    if col.size().getInfo() == 0: 
        return None
    return compute_carbon(col.median())


def get_carbon_year(year):
    start_date = ee.Date(f"{year}-01-01")
    end_date = ee.Date(f"{year}-12-31")

    col = (
        ee.ImageCollection("COPERNICUS/S2_SR_HARMONIZED")
        .filterBounds(aoi)
        .filterDate(start_date, end_date)
        .filter(ee.Filter.lt("CLOUDY_PIXEL_PERCENTAGE", 80))
        .map(mask_s2)
    )
    if col.size().getInfo() == 0: 
        return None
    return compute_carbon(col.median())


# ======================
# MAP UI
# ======================
@app.get("/map", response_class=HTMLResponse)
def map_view():
    html = """
    <!DOCTYPE html>
    <html>
    <head>
        <title>Carbon Multi-Scale Dashboard</title>
        <link rel="stylesheet" href="https://unpkg.com/leaflet/dist/leaflet.css"/>
        <script src="https://unpkg.com/leaflet/dist/leaflet.js"></script>
        <link rel="stylesheet" href="https://fonts.googleapis.com/css2?family=Inter:wght@400;500;600&family=Space+Grotesk:wght@500&family=IBM+Plex+Mono&display=swap">
        
        <style>
            html, body { margin:0; height:100%; background:#F7F8F5; overflow:hidden; }
            #map { height:100vh; width: calc(100% - 280px); margin-left: 280px; }

            .sidebar {
                position:absolute; top:0; left:0; bottom:0;
                width:280px; background:#F7F8F5; border-right:1px solid #E0E3DC;
                z-index:9999; padding:20px 18px; box-sizing:border-box;
                font-family:'Inter', sans-serif; color:#1B2430;
                display:flex; flex-direction:column; overflow-y:auto;
            }
            .live-dot { width:7px; height:7px; border-radius:50%; background:#3B6D11; display:inline-block; }
            .title { font-family:'Space Grotesk', sans-serif; font-weight:500; font-size:18px; margin-top:10px; }
            .coords { font-family:'IBM Plex Mono', monospace; font-size:11px; color:#6B7688; margin-top:4px; }
            .divider { height:1px; background:#E0E3DC; margin:16px 0; }

            .navbtn {
                all:unset; cursor:pointer; display:flex; align-items:center; gap:10px;
                padding:9px 10px; border-radius:8px; font-size:13px; color:#1B2430;
                border-left:2px solid transparent; margin-bottom:4px; transition: all 0.2s;
            }
            .navbtn:hover { background: rgba(0,0,0,0.04); }
            .navbtn.active { background:#E1F5EE; border-left-color:#0F6E56; font-weight: 500; }

            .readout { background:#FFFFFF; border:1px solid #E0E3DC; border-radius:10px; padding:12px; margin-top:10px;}
            .readout .label { font-size:11px; color:#6B7688; font-weight:600; text-transform: uppercase;}
            .readout .value { font-family:'IBM Plex Mono', monospace; font-size:12px; margin-top:4px; color:#1B2430; word-break: break-all;}

            select, input[type="date"] {
                width:100%; background:#FFFFFF; border:1px solid #E0E3DC;
                color:#1B2430; border-radius:8px; padding:7px 8px; box-sizing:border-box;
                font-family:'IBM Plex Mono', monospace; font-size:12px; margin-top:6px;
            }
            .input-group { display:none; margin-top:4px; }

            .legend-container { 
                margin-top: 20px; 
                padding-top: 10px; 
            }
            .legend-wrapper {
                display: flex;
                align-items: center;
                gap: 8px;
                margin-top: 8px;
            }
            .legend-bar { 
                width: 14px; 
                height: 70px; 
                border-radius: 4px;
                background: linear-gradient(180deg, #fde725, #5ec962, #21918c, #3b528b, #440154); 
                flex-shrink: 0;
            }
            .legend-ticks { 
                display: flex; 
                flex-direction: column; 
                justify-content: space-between;
                height: 70px; 
                font-family: 'IBM Plex Mono', monospace; 
                font-size: 11px; 
                color: #6B7688; 
                line-height: 1;
            }

            .leaflet-control-layers {
                border: 1px solid #E0E3DC !important;
                box-shadow: 0 2px 8px rgba(0,0,0,0.1) !important;
                border-radius: 8px !important;
                font-family: 'Inter', sans-serif;
                font-size: 12px;
            }
        </style>
    </head>

    <body>

    <!-- SIDEBAR STRUKTUR -->
    <div class="sidebar">
        <div style="display:flex; align-items:center; gap:8px;">
            <span class="live-dot"></span>
            <span style="font-size:11px; font-weight:600; text-transform:uppercase; letter-spacing:0.5px; color:#3B6D11;">GEE Connected</span>
        </div>
        
        <div class="title">Carbon Multi-Scale</div>
        <div class="coords">Padang Area [100.36, -0.92]</div>
        
        <div class="divider"></div>
        
        <!-- NAVIGASI MENU -->
        <button class="navbtn active" id="btn-7day" onclick="switchMode('7day')"> 7-Day Window</button>
        <button class="navbtn" id="btn-calendar" onclick="switchMode('calendar')"> 30-Day Calendar</button>
        <button class="navbtn" id="btn-monthly" onclick="switchMode('monthly')"> Monthly Data</button>
        <button class="navbtn" id="btn-yearly" onclick="switchMode('yearly')"> Yearly Data</button>
        
        <div class="divider"></div>

        <!-- KALENDER CONTROL -->
        <div id="calendarGroup" class="input-group">
            <span style="font-size:11px; color:#6B7688; font-weight:500;">Pilih Tanggal Akhir:</span>
            <input type="date" id="datePicker" onchange="loadCalendar()">
        </div>

        <!-- MONTHLY CONTROL -->
        <div id="monthlyGroup" class="input-group">
            <span style="font-size:11px; color:#6B7688; font-weight:500;">Pilih Bulan (Rentang Tahun 2024):</span>
            <select id="monthSelect" onchange="loadMonth()">
                <option value="1">Januari</option><option value="2">Februari</option>
                <option value="3">Maret</option><option value="4">April</option>
                <option value="5">Mei</option><option value="6">Juni</option>
                <option value="7">Juli</option><option value="8">Agustus</option>
                <option value="9">September</option><option value="10">Oktober</option>
                <option value="11">November</option><option value="12">Desember</option>
            </select>
        </div>

        <!-- YEARLY CONTROL -->
        <div id="yearlyGroup" class="input-group">
            <span style="font-size:11px; color:#6B7688; font-weight:500;">Pilih Tahun:</span>
            <select id="yearSelect" onchange="loadYear()">
                <option value="2022">2022</option>
                <option value="2023">2023</option>
                <option value="2024" selected>2024</option>
                <option value="2025">2025</option>
            </select>
        </div>

        <!-- OUTPUT STATUS PANEL -->
        <div class="readout">
            <div class="readout-block">
                <div class="label" id="label">Mode: 7-Day</div>
                <div class="value" id="dateLabel">-</div>
            </div>
        </div>

        <!-- LEGENDA WARNA -->
        <div class="legend-container">
            <div style="font-size:11px; font-weight:600; color:#6B7688; text-transform:uppercase; letter-spacing: 0.5px;">Estimasi Karbon</div>
            <div class="legend-wrapper">
                <div class="legend-bar"></div>
                <div class="legend-ticks">
                    <span>120 Max</span>
                    <span>60 Mid</span>
                    <span>0 Min</span>
                </div>
            </div>
        </div>
    </div>

    <!-- MAP CONTAINER -->
    <div id="map"></div>

    <script>
    // 1. DEFINISI PILIHAN BASEMAP
    const baseMaps = {
        "OpenStreetMap": L.tileLayer('https://tile.openstreetmap.org/{z}/{x}/{y}.png'),
        "Google Satellite": L.tileLayer('https://mt1.google.com/vt/lyrs=s&x={x}&y={y}&z={z}', { attribution: '© Google' }),
        "Google Hybrid": L.tileLayer('https://mt1.google.com/vt/lyrs=y&x={x}&y={y}&z={z}', { attribution: '© Google Maps' }),
        "Esri World Imagery": L.tileLayer('https://server.arcgisonline.com/ArcGIS/rest/services/World_Imagery/MapServer/tile/{z}/{y}/{x}', { attribution: 'Tiles © Esri' }),
        "CartoDB Dark Matter": L.tileLayer('https://{s}.basemaps.cartocdn.com/dark_all/{z}/{x}/{y}{r}.png', { attribution: '© CartoDB' })
    };

    // Inisialisasi map dengan default basemap OpenStreetMap
    const map = L.map('map', {
        center: [-0.9242544, 100.3624642],
        zoom: 11,
        layers: [baseMaps["OpenStreetMap"]]
    });

    // SOLUSI UTAMA: Membuat Pane khusus dengan z-index tinggi agar data Carbon selalu di atas basemap
    map.createPane('carbonPane');
    map.getPane('carbonPane').style.zIndex = 650; // Di atas TileLayer basemap standar (200-400)
    map.getPane('carbonPane').style.pointerEvents = 'none';

    // Wadah khusus (Layer Group) pelindung layer GEE
    const carbonLayerGroup = L.layerGroup().addTo(map);

    // Tambahkan pengontrol basemap di kanan atas peta
    L.control.layers(baseMaps, null, { position: 'topright', collapsed: true }).addTo(map);

    // Set default tanggal kalender ke hari ini
    document.getElementById('datePicker').value = new Date().toISOString().split('T')[0];

    function clearLayers(){
        carbonLayerGroup.clearLayers();
    }

    function switchMode(mode) {
        document.querySelectorAll('.navbtn').forEach(btn => btn.classList.remove('active'));
        document.getElementById("calendarGroup").style.display = "none";
        document.getElementById("monthlyGroup").style.display = "none";
        document.getElementById("yearlyGroup").style.display = "none";

        if (mode === '7day') {
            document.getElementById('btn-7day').classList.add('active');
            load7day();
        } else if (mode === 'calendar') {
            document.getElementById('btn-calendar').classList.add('active');
            document.getElementById("calendarGroup").style.display = "block";
            loadCalendar();
        } else if (mode === 'monthly') {
            document.getElementById('btn-monthly').classList.add('active');
            document.getElementById("monthlyGroup").style.display = "block";
            loadMonth();
        } else if (mode === 'yearly') {
            document.getElementById('btn-yearly').classList.add('active');
            document.getElementById("yearlyGroup").style.display = "block";
            loadYear();
        }
    }

    function load7day(){
        document.getElementById("label").innerText = "Mode: 7-Day Window";
        document.getElementById("dateLabel").innerText = "Memuat data...";

        fetch("/carbon-month")
        .then(r => r.json())
        .then(data => {
            clearLayers();
            if(!data || data.length === 0) {
                document.getElementById("dateLabel").innerText = "Data tidak tersedia.";
                return;
            }
            data.forEach((item, i) => {
                // Diarahkan menggunakan pane khusus agar terbebas dari tindihan basemap
                let layer = L.tileLayer(item.tile_url, {opacity:0.7, pane: 'carbonPane'});
                if(i === 0){
                    layer.addTo(carbonLayerGroup);
                    document.getElementById("dateLabel").innerText = item.label;
                }
            });
        });
    }

    function loadCalendar(){
        const endDateVal = document.getElementById("datePicker").value;
        if (!endDateVal) return;

        let endDate = new Date(endDateVal);
        let startDate = new Date(endDate);
        startDate.setDate(startDate.getDate() - 30);

        const startStr = startDate.toISOString().split('T')[0];
        const endStr = endDate.toISOString().split('T')[0];

        document.getElementById("label").innerText = "Mode: 30-Day Calendar";
        document.getElementById("dateLabel").innerText = "Memproses data GEE...";

        fetch(`/carbon-custom-range?start_date=${startStr}&end_date=${endStr}`)
        .then(r => r.json())
        .then(data => {
            clearLayers();
            if (data.error) {
                document.getElementById("dateLabel").innerText = "Error: " + data.error;
                return;
            }
            let layer = L.tileLayer(data.tile_url, {opacity:0.7, pane: 'carbonPane'});
            layer.addTo(carbonLayerGroup);
            document.getElementById("dateLabel").innerText = `${startStr} s/d ${endStr}`;
        })
        .catch(err => {
            document.getElementById("dateLabel").innerText = "Gagal memuat data.";
        });
    }

    function loadMonth(){
        const month = document.getElementById("monthSelect").value;
        const monthText = document.getElementById("monthSelect").options[document.getElementById("monthSelect").selectedIndex].text;
        document.getElementById("label").innerText = "Mode: Monthly Composite";
        document.getElementById("dateLabel").innerText = "Memuat data...";

        fetch(`/carbon-month-single/2024/${month}`)
        .then(r => r.json())
        .then(data => {
            clearLayers();
            if (data.error) {
                document.getElementById("dateLabel").innerText = "Data kosong/tidak ditemukan.";
                return;
            }
            let layer = L.tileLayer(data.tile_url, {opacity:0.7, pane: 'carbonPane'});
            layer.addTo(carbonLayerGroup);
            document.getElementById("dateLabel").innerText = monthText + " 2024";
        });
    }

    function loadYear(){
        const year = document.getElementById("yearSelect").value;
        document.getElementById("label").innerText = "Mode: Yearly Composite";
        document.getElementById("dateLabel").innerText = "Memuat data...";

        fetch(`/carbon-year/${year}`)
        .then(r => r.json())
        .then(data => {
            clearLayers();
            if (data.error) {
                document.getElementById("dateLabel").innerText = "Data kosong/tidak ditemukan.";
                return;
            }
            let layer = L.tileLayer(data.tile_url, {opacity:0.7, pane: 'carbonPane'});
            layer.addTo(carbonLayerGroup);
            document.getElementById("dateLabel").innerText = "Tahun " + year;
        });
    }

    // Load default run pertama kali
    load7day();
    </script>
    </body>
    </html>
    """
    return HTMLResponse(html)

# ======================
# ENDPOINTS
# ======================
@app.get("/carbon-month")
def carbon_month():
    windows = [(0,7),(7,14),(14,21),(21,28)]
    today = datetime.datetime.utcnow().date()
    vis = {"min": 0, "max": 120, "palette": ["#440154","#3b528b","#21918c","#5ec962","#fde725"]}
    result = []
    for s,e in windows:
        start_date = today - datetime.timedelta(days=e)
        end_date = today - datetime.timedelta(days=s)
        carbon = get_carbon_window(s,e)
        if carbon is None: continue
        map_id = carbon.getMapId(vis)
        result.append({
            "label": f"{start_date} → {end_date}",
            "tile_url": map_id["tile_fetcher"].url_format
        })
    return result

@app.get("/carbon-custom-range")
def carbon_custom_range(start_date: str, end_date: str):
    carbon = get_carbon_custom_range(start_date, end_date)
    if carbon is None:
        return {"error": "Tidak ada data satelit bebas awan pada rentang ini"}
    vis = {"min": 0, "max": 120, "palette": ["#440154","#3b528b","#21918c","#5ec962","#fde725"]}
    map_id = carbon.getMapId(vis)
    return {"tile_url": map_id["tile_fetcher"].url_format}

@app.get("/carbon-month-single/{year}/{month}")
def carbon_month_single(year:int, month:int):
    carbon = get_carbon_month(year, month)
    if carbon is None: return {"error":"no data"}
    vis = {"min": 0, "max": 120, "palette": ["#440154","#3b528b","#21918c","#5ec962","#fde725"]}
    map_id = carbon.getMapId(vis)
    return {"tile_url": map_id["tile_fetcher"].url_format}

@app.get("/carbon-year/{year}")
def carbon_year(year:int):
    carbon = get_carbon_year(year)
    if carbon is None: return {"error":"no data"}
    vis = {"min": 0, "max": 120, "palette": ["#440154","#3b528b","#21918c","#5ec962","#fde725"]}
    map_id = carbon.getMapId(vis)
    return {"tile_url": map_id["tile_fetcher"].url_format}

@app.get("/")
def home():
    return {"status": "Carbon Multi-scale Ready", "map": "/map"}