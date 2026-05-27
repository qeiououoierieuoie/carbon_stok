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
# 7-DAY WINDOW
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


# ======================
# MONTHLY
# ======================
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


# ======================
# YEARLY
# ======================
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

        <style>
            html, body { margin:0; height:100%; }
            #map { height:100vh; }

            .panel {
                position:absolute;
                top:10px;
                left:50%;
                transform:translateX(-50%);
                background:white;
                padding:12px;
                border-radius:10px;
                z-index:9999;
                width:360px;
                box-shadow:0 2px 10px rgba(0,0,0,0.3);
            }

            button { margin:4px; }
            select { width:100%; margin-top:5px; }
        </style>
    </head>

    <body>

    <div id="map"></div>

    <div class="panel">

        <b>Carbon Multi-Scale Dashboard</b><br><br>

        <button onclick="load7day()">7-Day</button>
        <button onclick="loadMonth()">Monthly</button>
        <button onclick="loadYear()">Yearly</button>

        <br>

        <select id="monthSelect" style="display:none;" onchange="loadMonth()">
            <option value="1">Jan</option><option value="2">Feb</option>
            <option value="3">Mar</option><option value="4">Apr</option>
            <option value="5">May</option><option value="6">Jun</option>
            <option value="7">Jul</option><option value="8">Aug</option>
            <option value="9">Sep</option><option value="10">Oct</option>
            <option value="11">Nov</option><option value="12">Dec</option>
        </select>

        <select id="yearSelect" style="display:none;" onchange="loadYear()">
            <option>2022</option>
            <option>2023</option>
            <option selected>2024</option>
            <option>2025</option>
        </select>

        <div id="label" style="margin-top:10px;">Mode: 7-Day</div>

        <!-- DATE LABEL -->
        <div id="dateLabel" style="margin-top:8px;font-weight:bold;">
            -
        </div>

    </div>

    <script>

    const map = L.map('map').setView([-0.9242544, 100.3624642], 11);

    L.tileLayer('https://tile.openstreetmap.org/{z}/{x}/{y}.png').addTo(map);

    let layers = [];

    function clearLayers(){
        layers.forEach(l => map.removeLayer(l));
        layers = [];
    }

    // ======================
    // 7-DAY MODE
    // ======================
    function load7day(){

        document.getElementById("label").innerText = "Mode: 7-Day";

        fetch("/carbon-month")
        .then(r => r.json())
        .then(data => {

            clearLayers();

            data.forEach((item,i) => {

                let layer = L.tileLayer(item.tile_url,{opacity:0.7});
                layers.push(layer);

                if(i===0){
                    layer.addTo(map);

                    document.getElementById("dateLabel").innerText =
                        "Periode: " + item.label;
                }
            });
        });
    }

    // ======================
    // MONTHLY MODE
    // ======================
    function loadMonth(){

        const month = document.getElementById("monthSelect").value;

        document.getElementById("monthSelect").style.display = "block";

        fetch(`/carbon-month-single/2024/${month}`)
        .then(r => r.json())
        .then(data => {

            clearLayers();

            let layer = L.tileLayer(data.tile_url,{opacity:0.7});
            layers.push(layer);
            layer.addTo(map);

            document.getElementById("label").innerText =
                "Mode: Monthly " + month;

            document.getElementById("dateLabel").innerText =
                "Bulan: " + month + " / 2024";
        });
    }

    // ======================
    // YEARLY MODE
    // ======================
    function loadYear(){

        const year = document.getElementById("yearSelect").value;

        document.getElementById("yearSelect").style.display = "block";

        fetch(`/carbon-year/${year}`)
        .then(r => r.json())
        .then(data => {

            clearLayers();

            let layer = L.tileLayer(data.tile_url,{opacity:0.7});
            layers.push(layer);
            layer.addTo(map);

            document.getElementById("label").innerText =
                "Mode: Yearly " + year;

            document.getElementById("dateLabel").innerText =
                "Tahun: " + year;
        });
    }

    load7day();

    </script>

    </body>
    </html>
    """

    return HTMLResponse(html)


# ======================
# 7-DAY ENDPOINT
# ======================
@app.get("/carbon-month")
def carbon_month():

    windows = [(0,7),(7,14),(14,21),(21,28)]

    today = datetime.datetime.utcnow().date()

    vis = {
        "min": 0,
        "max": 120,
        "palette": ["#440154","#3b528b","#21918c","#5ec962","#fde725"]
    }

    result = []

    for s,e in windows:

        start_date = today - datetime.timedelta(days=e)
        end_date = today - datetime.timedelta(days=s)

        carbon = get_carbon_window(s,e)

        if carbon is None:
            continue

        map_id = carbon.getMapId(vis)

        result.append({
            "label": f"{start_date} → {end_date}",
            "tile_url": map_id["tile_fetcher"].url_format
        })

    return result


# ======================
# MONTHLY SINGLE
# ======================
@app.get("/carbon-month-single/{year}/{month}")
def carbon_month_single(year:int, month:int):

    carbon = get_carbon_month(year, month)

    if carbon is None:
        return {"error":"no data"}

    vis = {
        "min": 0,
        "max": 120,
        "palette": ["#440154","#3b528b","#21918c","#5ec962","#fde725"]
    }

    map_id = carbon.getMapId(vis)

    return {
        "tile_url": map_id["tile_fetcher"].url_format
    }


# ======================
# YEARLY
# ======================
@app.get("/carbon-year/{year}")
def carbon_year(year:int):

    carbon = get_carbon_year(year)

    if carbon is None:
        return {"error":"no data"}

    vis = {
        "min": 0,
        "max": 120,
        "palette": ["#440154","#3b528b","#21918c","#5ec962","#fde725"]
    }

    map_id = carbon.getMapId(vis)

    return {
        "tile_url": map_id["tile_fetcher"].url_format
    }


# ======================
# ROOT
# ======================
@app.get("/")
def home():
    return {
        "status": "Carbon Multi-scale Ready",
        "map": "/map"
    }