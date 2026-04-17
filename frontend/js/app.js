// ── Configuration ──
const API_BASE = window.APP_CONFIG?.API_BASE_URL || "";
const WS_URL = window.APP_CONFIG?.WEBSOCKET_URL || "";


let currentUser = null;
let authToken = null;

// Helper function to parse JWT token
function parseJWT(token) {
    try {
        const base64Url = token.split('.')[1];
        const base64 = base64Url.replace(/-/g, '+').replace(/_/g, '/');
        const jsonPayload = decodeURIComponent(atob(base64).split('').map(function(c) {
            return '%' + ('00' + c.charCodeAt(0).toString(16)).slice(-2);
        }).join(''));
        return JSON.parse(jsonPayload);
    } catch (e) {
        console.error("Error parsing JWT:", e);
        return {};
    }
}
let cart = { items: [] }; // Removed restaurant_id - now supports multiple restaurants
let pendingVerificationEmail = null;
let previousPage = "home"; // Track where we came from

// WebSocket connection manager
let wsConnection = null;
let wsReconnectAttempts = 0;
const WS_MAX_RECONNECT_ATTEMPTS = 5;
const WS_RECONNECT_DELAY = 2000; // 2 seconds

// Map tracking
let trackingMap = null;
let driverMarker = null;
let restaurantMarker = null;
let destinationMarker = null;
let routeLine = null;

// Route animation state
let currentRoute = null;
let routeAnimationInterval = null;
let animationProgress = 0;
let animationDuration = 60000; // 60 seconds for full route
let isAnimating = false; // Flag to prevent duplicate animations
let currentOrderData = null;
let currentTrackedOrder = null; // Full order object for tracking page


// ── WebSocket Functions ──

function connectWebSocket() {
    if (!authToken) {
        console.log("Cannot connect WebSocket: not authenticated");
        return;
    }

    if (!WS_URL) {
        console.error("WebSocket URL not configured");
        return;
    }

    // Add JWT token as query parameter (used by authorizer)
    const wsUrl = `${WS_URL}?token=${encodeURIComponent(authToken)}`;

    try {
        wsConnection = new WebSocket(wsUrl);

        wsConnection.onopen = () => {
            console.log("WebSocket connected");
            wsReconnectAttempts = 0;
            showToast("Real-time tracking connected", "success");
        };

        wsConnection.onmessage = (event) => {
            console.log("WebSocket RAW message:", event.data);
            const data = JSON.parse(event.data);
            console.log("WebSocket PARSED message:", data);
            handleWebSocketMessage(data);
        };

        wsConnection.onerror = (error) => {
            console.error("WebSocket error:", error);
        };

        wsConnection.onclose = () => {
            console.log("WebSocket disconnected");
            wsConnection = null;

            // Attempt reconnection
            if (wsReconnectAttempts < WS_MAX_RECONNECT_ATTEMPTS) {
                wsReconnectAttempts++;
                console.log(`Reconnecting... attempt ${wsReconnectAttempts}`);
                setTimeout(connectWebSocket, WS_RECONNECT_DELAY * wsReconnectAttempts);
            }
        };
    } catch (error) {
        console.error("Failed to create WebSocket:", error);
    }
}

function disconnectWebSocket() {
    if (wsConnection && wsConnection.readyState === WebSocket.OPEN) {
        wsConnection.close();
    }
    wsConnection = null;
}

function sendWebSocketMessage(action, data) {
    if (!wsConnection || wsConnection.readyState !== WebSocket.OPEN) {
        console.error("WebSocket not connected");
        return false;
    }

    wsConnection.send(JSON.stringify({ action, ...data }));
    return true;
}

function handleWebSocketMessage(data) {
    console.log("WebSocket message received:", data);

    // Handle both 'type' and 'action' fields (backend might use either)
    const messageType = data.type || data.action;

    console.log("Message type:", messageType);

    switch (messageType) {
        case "status":
            updateOrderStatus(data);
            break;
        case "location":
        case "locationUpdate":  // From sendLocation route
            console.log("Calling updateDriverLocation with:", data);
            updateDriverLocation(data);
            break;
        case "driver_offer":  // Driver delivery offer
            handleDriverOffer(data);
            break;
        default:
            console.log("Unknown WebSocket message type:", messageType, data);
    }
}

async function updateOrderStatus(data) {
    console.log("Order status update:", data);

    const status = (data.status || "").toLowerCase();

    // Update order card in orders list if visible
    const orderCard = document.querySelector(`[data-order-id="${data.order_id}"]`);
    if (orderCard) {
        const statusBadge = orderCard.querySelector(".order-status");
        if (statusBadge) {
            statusBadge.textContent = data.status.toUpperCase();
            statusBadge.className = `order-status status-${status}`;
        }
    }

    // Update status badge on tracking page (top right)
    const trackingStatusBadges = document.querySelectorAll(".order-status");
    trackingStatusBadges.forEach(badge => {
        // Only update if it's on the tracking page for this order
        const trackingInfo = document.getElementById("tracking-info");
        if (trackingInfo && trackingInfo.innerHTML.includes(data.order_id.substring(0, 8))) {
            badge.textContent = data.status.toUpperCase();
            badge.className = `order-status status-${status}`;
        }
    });

    // Update tracking timeline if on tracking page
    updateTrackingTimeline(data.order_id, data.status);

    // Update the status-based info section in-place (without full page reload)
    const trackingPage = document.getElementById("page-tracking");
    if (trackingPage && trackingPage.style.display !== "none") {
        const trackingInfo = document.getElementById("tracking-info");
        if (trackingInfo && trackingInfo.innerHTML.includes(data.order_id.substring(0, 8))) {
            console.log("Updating status-based info in-place");
            // Find and update the status-based info section
            updateStatusBasedInfo(data.status, data.order_id, data.delivery_id);
        }
    }

    // HYBRID APPROACH: Show driver position based on delivery workflow
    // Get delivery_id from WebSocket message (now included) or fallback to currentOrderData
    const deliveryId = data.delivery_id || (currentOrderData && currentOrderData.delivery_id);

    if ((status === "driver_assigned" || status === "picked_up" || status === "delivering" || status === "delivered") && deliveryId && trackingMap && currentOrderData) {
        console.log(`🚗 [HYBRID] Driver position update for status: ${status.toUpperCase()}`);

        const restaurantLoc = parseLocation(currentOrderData.restaurant_location, { lat: 37.7849, lng: -122.4094 });
        const deliveryLoc = parseLocation(currentOrderData.delivery_address, { lat: 37.7749, lng: -122.4194 });
        let driverLoc = null;

        try {
            if (status === "picked_up" || status === "delivering") {
                // LOGICAL POSITION: Driver at restaurant, ready to deliver to customer
                // Start animation immediately (synchronous with driver's page)
                console.log(`🚗 [${status.toUpperCase()}] Driver starts journey from restaurant to customer`);
                driverLoc = restaurantLoc;

            } else if (status === "delivered") {
                // LOGICAL POSITION: Driver just delivered at customer location
                console.log("🏠 [DELIVERED] Snapping driver to customer location");
                driverLoc = deliveryLoc;

            } else if (status === "driver_assigned") {
                // LOGICAL POSITION: Driver at restaurant (waiting to pick up)
                console.log("📍 [DRIVER_ASSIGNED] Driver at restaurant (ready to pick up)");
                driverLoc = restaurantLoc;
            }

            // Update map with driver position
            if (driverLoc && driverLoc.lat && driverLoc.lng) {
                console.log(`🗺️ Updating map: Driver at [${driverLoc.lat.toFixed(4)}, ${driverLoc.lng.toFixed(4)}]`);
                updateDriverMarkerOnMap(driverLoc.lat, driverLoc.lng);
                // Redraw route with animation based on new status
                await drawRoute(restaurantLoc, driverLoc, deliveryLoc, status);
                fitMapBounds(restaurantLoc, deliveryLoc, driverLoc, status);
                console.log("✅ Driver marker and route updated successfully");
            } else {
                console.log("❌ No valid driver location - marker not shown");
            }

        } catch (err) {
            console.error("❌ Failed to update driver position:", err);
        }
    }

    // Show notification
    const statusMessages = {
        "confirmed": "Your order has been confirmed!",
        "preparing": "Restaurant is preparing your food",
        "driver_assigned": "Driver is heading to the restaurant!",
        "picked_up": "Driver picked up your order!",
        "delivering": "Driver is on the way!",
        "completed": "Order delivered! Enjoy your meal!",
        "delivered": "Order delivered! Enjoy your meal!",
        "cancelled": "Order has been cancelled"
    };

    const message = statusMessages[status] || `Order status: ${data.status}`;
    showToast(message, status === "cancelled" ? "error" : "success");
}

function updateStatusBasedInfo(status, orderId, deliveryId = null) {
    // Find the status-based info container
    const statusInfoContainer = document.getElementById("status-based-info");
    if (!statusInfoContainer) {
        console.log("Status info container not found");
        return;
    }

    // Update the stored order's status and delivery_id
    if (currentTrackedOrder) {
        currentTrackedOrder.status = status;
        if (deliveryId) {
            currentTrackedOrder.delivery_id = deliveryId;
            console.log("Updated currentTrackedOrder with delivery_id:", deliveryId);
        }
    } else {
        console.log("No current tracked order");
        return;
    }

    // Get the new HTML for status-based info
    const newStatusHTML = getStatusBasedInfo(currentTrackedOrder);

    // Replace the content
    statusInfoContainer.innerHTML = newStatusHTML;
    console.log("Status-based info updated to:", status);
}

function initializeTrackingMap(orderData) {
    const mapContainer = document.getElementById("tracking-map");
    const mapLegend = document.getElementById("map-legend");
    if (!mapContainer) return;

    // Store order data for updates
    currentOrderData = orderData;

    // Show map container and legend
    mapContainer.style.display = "block";
    if (mapLegend) mapLegend.style.display = "block";

    // Parse locations with fallbacks to San Francisco area
    const restaurantLoc = parseLocation(orderData.restaurant_location, { lat: 37.7849, lng: -122.4094 });
    const deliveryLoc = parseLocation(orderData.delivery_address, { lat: 37.7749, lng: -122.4194 });
    const driverLoc = orderData.driver_location ? parseLocation(orderData.driver_location) : null;

    // Initialize map if not already done
    if (!trackingMap) {
        const centerLat = restaurantLoc.lat;
        const centerLng = restaurantLoc.lng;
        trackingMap = L.map("tracking-map").setView([centerLat, centerLng], 13);

        // Add OpenStreetMap tiles
        L.tileLayer("https://{s}.tile.openstreetmap.org/{z}/{x}/{y}.png", {
            attribution: '&copy; <a href="https://www.openstreetmap.org/copyright">OpenStreetMap</a>',
            maxZoom: 19,
        }).addTo(trackingMap);
    }

    // Add restaurant marker
    if (!restaurantMarker && restaurantLoc) {
        restaurantMarker = L.marker([restaurantLoc.lat, restaurantLoc.lng], {
            icon: L.divIcon({
                className: "restaurant-marker",
                html: '<div style="background: #457b9d; color: white; padding: 10px; border-radius: 50%; width: 45px; height: 45px; display: flex; align-items: center; justify-content: center; font-size: 22px; box-shadow: 0 4px 12px rgba(0,0,0,0.3); border: 3px solid white;">🍽️</div>',
                iconSize: [45, 45],
                iconAnchor: [22.5, 22.5],
            }),
        }).addTo(trackingMap);
        restaurantMarker.bindPopup("<b>Restaurant</b><br>Preparing your order");
    }

    // Add destination marker
    if (!destinationMarker && deliveryLoc) {
        destinationMarker = L.marker([deliveryLoc.lat, deliveryLoc.lng], {
            icon: L.divIcon({
                className: "destination-marker",
                html: '<div style="background: #2d6a4f; color: white; padding: 10px; border-radius: 50%; width: 45px; height: 45px; display: flex; align-items: center; justify-content: center; font-size: 22px; box-shadow: 0 4px 12px rgba(0,0,0,0.3); border: 3px solid white;">🏠</div>',
                iconSize: [45, 45],
                iconAnchor: [22.5, 22.5],
            }),
        }).addTo(trackingMap);
        destinationMarker.bindPopup("<b>Delivery Address</b><br>Your location");
    }

    // Add driver marker if available
    console.log("Driver location parsed:", driverLoc);
    if (driverLoc && driverLoc.lat && driverLoc.lng) {
        console.log("Adding driver marker at:", driverLoc.lat, driverLoc.lng);
        updateDriverMarkerOnMap(driverLoc.lat, driverLoc.lng);

        // Draw route with animation based on order status
        drawRoute(restaurantLoc, driverLoc, deliveryLoc, orderData.status);
    } else {
        console.log("No valid driver location, drawing route without driver");
        // Draw direct line from restaurant to destination (before driver assigned)
        drawRoute(restaurantLoc, null, deliveryLoc, orderData.status);
    }

    // Fit bounds to show all markers
    fitMapBounds(restaurantLoc, deliveryLoc, driverLoc, orderData.status);
}

function parseLocation(locationData, fallback = null) {
    if (!locationData) return fallback;

    console.log("Parsing location data:", locationData);

    // Handle different formats: {lat, lng}, "lat,lng", {latitude, longitude}
    if (typeof locationData === 'string') {
        const parts = locationData.split(',');
        if (parts.length === 2) {
            const parsed = { lat: parseFloat(parts[0]), lng: parseFloat(parts[1]) };
            console.log("Parsed from string:", parsed);
            return parsed;
        }
    } else if (typeof locationData === 'object') {
        // Check for empty object
        if (Object.keys(locationData).length === 0) {
            console.log("Empty location object, using fallback");
            return fallback;
        }

        // Standard format: {lat, lng}
        if (locationData.lat !== undefined && locationData.lng !== undefined) {
            const parsed = { lat: parseFloat(locationData.lat), lng: parseFloat(locationData.lng) };
            console.log("Parsed from lat/lng:", parsed);
            return parsed;
        }

        // Alternative format: {latitude, longitude}
        if (locationData.latitude !== undefined && locationData.longitude !== undefined) {
            const parsed = { lat: parseFloat(locationData.latitude), lng: parseFloat(locationData.longitude) };
            console.log("Parsed from latitude/longitude:", parsed);
            return parsed;
        }

        // DynamoDB Decimal format: numbers might be strings
        if (typeof locationData.lat === 'string' && typeof locationData.lng === 'string') {
            const parsed = { lat: parseFloat(locationData.lat), lng: parseFloat(locationData.lng) };
            console.log("Parsed from string numbers:", parsed);
            return parsed;
        }
    }

    console.log("Could not parse location, using fallback:", fallback);
    return fallback;
}

/**
 * Fetch actual driving route from OSRM API
 * @param {Object} from - Starting location {lat, lng}
 * @param {Object} to - Destination location {lat, lng}
 * @returns {Promise<Array>} - Array of [lat, lng] coordinates along the route
 */
async function fetchRoute(from, to) {
    try {
        // OSRM requires lng,lat format (not lat,lng!)
        const url = `https://router.project-osrm.org/route/v1/driving/${from.lng},${from.lat};${to.lng},${to.lat}?overview=full&geometries=geojson`;

        console.log(`🗺️ [ROUTE] Fetching route from (${from.lat}, ${from.lng}) to (${to.lat}, ${to.lng})`);

        const response = await fetch(url);
        const data = await response.json();

        if (data.code !== 'Ok' || !data.routes || data.routes.length === 0) {
            console.warn("OSRM routing failed, using straight line");
            return [[from.lat, from.lng], [to.lat, to.lng]];
        }

        // Extract coordinates from GeoJSON (they're in [lng, lat] format)
        const coordinates = data.routes[0].geometry.coordinates.map(coord => [coord[1], coord[0]]);
        console.log(`✅ [ROUTE] Fetched route with ${coordinates.length} points`);

        return coordinates;
    } catch (error) {
        console.error("Error fetching route:", error);
        // Fallback to straight line
        return [[from.lat, from.lng], [to.lat, to.lng]];
    }
}

/**
 * Stop any ongoing route animation
 */
function stopRouteAnimation() {
    if (routeAnimationInterval) {
        clearInterval(routeAnimationInterval);
        routeAnimationInterval = null;
        console.log("⏹️ [ANIMATION] Stopped");
    }
    animationProgress = 0;
    isAnimating = false;
}

/**
 * Animate driver marker along a route
 * @param {Array} route - Array of [lat, lng] coordinates
 * @param {number} duration - Animation duration in milliseconds
 * @param {string} orderStatus - Current order status for logging
 */
function animateDriverAlongRoute(route, duration, orderStatus) {
    if (!route || route.length < 2) {
        console.warn("Cannot animate: route too short");
        return;
    }

    // Prevent duplicate animations - only start if not already animating
    if (isAnimating) {
        console.log("⚠️ [ANIMATION] Already in progress, skipping duplicate start");
        return;
    }

    // Stop any existing animation
    stopRouteAnimation();

    console.log(`🎬 [ANIMATION] Starting animation along ${route.length} points over ${duration/1000}s (status: ${orderStatus})`);

    isAnimating = true;
    animationProgress = 0;
    const startTime = Date.now();

    routeAnimationInterval = setInterval(() => {
        const elapsed = Date.now() - startTime;
        const progress = Math.min(elapsed / duration, 1.0); // 0 to 1

        // Find position along route based on progress
        const totalPoints = route.length - 1;
        const currentIndex = Math.floor(progress * totalPoints);
        const nextIndex = Math.min(currentIndex + 1, totalPoints);

        // Interpolate between current and next point
        const segmentProgress = (progress * totalPoints) - currentIndex;
        const currentPoint = route[currentIndex];
        const nextPoint = route[nextIndex];

        const lat = currentPoint[0] + (nextPoint[0] - currentPoint[0]) * segmentProgress;
        const lng = currentPoint[1] + (nextPoint[1] - currentPoint[1]) * segmentProgress;

        // Update driver marker position
        updateDriverMarkerOnMap(lat, lng);

        animationProgress = progress;

        // Stop when complete
        if (progress >= 1.0) {
            console.log("✅ [ANIMATION] Completed");
            stopRouteAnimation();
        }
    }, 100); // Update every 100ms for smooth animation
}

async function drawRoute(restaurantLoc, driverLoc, deliveryLoc, orderStatus) {
    // Remove existing route line
    if (routeLine) {
        trackingMap.removeLayer(routeLine);
    }

    const status = (orderStatus || "").toLowerCase();
    let routeCoordinates = [];

    if (!driverLoc) {
        // No driver yet: Draw simple route Restaurant -> Destination (dashed)
        console.log("📍 [ROUTE] No driver assigned, showing restaurant-to-destination route");
        routeCoordinates = await fetchRoute(restaurantLoc, deliveryLoc);

        routeLine = L.polyline(routeCoordinates, {
            color: '#e63946',
            weight: 4,
            opacity: 0.5,
            dashArray: '10, 10',  // Dashed when no driver
        }).addTo(trackingMap);

        currentRoute = null;
        return;
    }

    // Driver is assigned - determine which route to show and animate
    if (status === "driver_assigned") {
        // Driver assigned but hasn't picked up yet - show static position, NO animation
        console.log(`📍 [ROUTE] Driver assigned (static) - showing route preview without animation`);

        // Show route from restaurant to customer (preview, dashed)
        routeCoordinates = await fetchRoute(restaurantLoc, deliveryLoc);
        routeLine = L.polyline(routeCoordinates, {
            color: '#4caf50',
            weight: 4,
            opacity: 0.5,
            dashArray: '10, 10',  // Dashed to indicate preview
        }).addTo(trackingMap);

        // NO animation - driver is stationary until pickup
        currentRoute = null;
        stopRouteAnimation();
        console.log("⏸️ [ROUTE] Driver stationary - waiting for pickup")

    } else if (status === "picked_up" || status === "delivering") {
        // Driver delivering from restaurant to customer
        // Start animation immediately (synchronous with driver's page)
        console.log(`🚗 [ROUTE] Driver → Customer (from restaurant) - status: ${status.toUpperCase()}`);

        // IMPORTANT: Driver starts from restaurant (where they just picked up)
        const startLoc = restaurantLoc;
        routeCoordinates = await fetchRoute(startLoc, deliveryLoc);

        // Draw the actual route
        routeLine = L.polyline(routeCoordinates, {
            color: '#4caf50',
            weight: 4,
            opacity: 0.7,
        }).addTo(trackingMap);

        // Store route for animation
        currentRoute = routeCoordinates;

        // Position driver marker at the START of the route (restaurant) before animating
        if (routeCoordinates && routeCoordinates.length > 0) {
            const startPoint = routeCoordinates[0];
            console.log(`📍 [ANIMATION SETUP] Positioning driver at route start: [${startPoint[0]}, ${startPoint[1]}]`);
            updateDriverMarkerOnMap(startPoint[0], startPoint[1]);
        }

        // Start animation along this route immediately
        animateDriverAlongRoute(routeCoordinates, animationDuration, status);

    } else if (status === "delivered") {
        // Delivery complete - driver at customer location
        console.log("🏠 [ROUTE] Delivered - no active route");
        currentRoute = null;
        stopRouteAnimation();

    } else {
        // Default: show full route Restaurant -> Customer
        routeCoordinates = await fetchRoute(restaurantLoc, deliveryLoc);
        routeLine = L.polyline(routeCoordinates, {
            color: '#e63946',
            weight: 4,
            opacity: 0.7,
        }).addTo(trackingMap);
    }
}

function updateDriverMarkerOnMap(lat, lng) {
    if (!driverMarker) {
        driverMarker = L.marker([lat, lng], {
            icon: L.divIcon({
                className: "driver-marker",
                html: '<div style="background: #e63946; color: white; padding: 10px; border-radius: 50%; width: 45px; height: 45px; display: flex; align-items: center; justify-content: center; font-size: 22px; box-shadow: 0 4px 12px rgba(0,0,0,0.3); border: 3px solid white; animation: pulse 2s infinite;">🚗</div>',
                iconSize: [45, 45],
                iconAnchor: [22.5, 22.5],
            }),
        }).addTo(trackingMap);
        driverMarker.bindPopup("<b>Driver</b><br>On the way to you!");
    } else {
        // Animate marker to new position
        driverMarker.setLatLng([lat, lng]);
    }
}

function fitMapBounds(restaurantLoc, deliveryLoc, driverLoc, orderStatus) {
    const bounds = [];

    if (restaurantLoc) bounds.push([restaurantLoc.lat, restaurantLoc.lng]);
    if (deliveryLoc) bounds.push([deliveryLoc.lat, deliveryLoc.lng]);
    if (driverLoc) bounds.push([driverLoc.lat, driverLoc.lng]);

    if (bounds.length > 0) {
        const latLngBounds = L.latLngBounds(bounds);
        trackingMap.fitBounds(latLngBounds, {
            padding: [80, 80],
            maxZoom: 15
        });
    }

    // Focus on specific marker based on status
    if (orderStatus === 'PREPARING' && restaurantLoc) {
        setTimeout(() => trackingMap.setView([restaurantLoc.lat, restaurantLoc.lng], 14), 500);
    } else if (orderStatus === 'DELIVERING' && driverLoc) {
        setTimeout(() => trackingMap.setView([driverLoc.lat, driverLoc.lng], 14), 500);
    }
}

function updateDriverLocation(data) {
    console.log("updateDriverLocation called with:", data);
    console.log("Map exists?", trackingMap !== null);
    console.log("Driver marker exists?", driverMarker !== null);

    const driverLoc = { lat: data.lat, lng: data.lng };
    console.log("Driver location:", driverLoc);

    // Update ETA display
    const etaElement = document.getElementById("tracking-eta");
    if (etaElement) {
        if (data.eta) {
            etaElement.innerHTML = `🕐 <strong>ETA:</strong> ${data.eta} minutes`;
        } else {
            // Calculate simple distance-based ETA (rough estimate)
            if (currentOrderData && currentOrderData.delivery_address) {
                const destLoc = parseLocation(currentOrderData.delivery_address);
                if (destLoc) {
                    const distance = calculateDistance(driverLoc.lat, driverLoc.lng, destLoc.lat, destLoc.lng);
                    const eta = Math.ceil(distance / 0.5); // Assume 30 km/h average speed
                    etaElement.innerHTML = `🕐 <strong>ETA:</strong> ~${eta} minutes`;
                }
            }
        }
    }

    // Update driver location display
    const locationElement = document.getElementById("driver-location");
    if (locationElement) {
        const timestamp = data.sent_at || data.timestamp || new Date().toISOString();
        const timeStr = new Date(timestamp).toLocaleTimeString();
        locationElement.innerHTML = `
            <div style="padding: 0.75rem; background: linear-gradient(135deg, #e63946 0%, #c1121f 100%); color: white; border-radius: 8px; margin-top: 0.75rem;">
                <div style="display: flex; align-items: center; margin-bottom: 0.5rem;">
                    <span style="font-size: 1.5rem; margin-right: 0.5rem;">🚗</span>
                    <strong style="font-size: 1rem;">Driver is on the way!</strong>
                </div>
                <span style="font-size: 0.8rem; opacity: 0.9;">Last updated: ${timeStr}</span>
            </div>
        `;
        showToast("📍 Driver location updated", "success");
    }

    // Update map marker and route
    if (trackingMap) {
        console.log("Updating driver marker on map...");
        updateDriverMarkerOnMap(driverLoc.lat, driverLoc.lng);

        if (currentOrderData) {
            // Redraw route with new driver position
            const restaurantLoc = parseLocation(currentOrderData.restaurant_location, { lat: 37.7849, lng: -122.4094 });
            const deliveryLoc = parseLocation(currentOrderData.delivery_address, { lat: 37.7749, lng: -122.4194 });
            console.log("Redrawing route:", restaurantLoc, driverLoc, deliveryLoc);
            drawRoute(restaurantLoc, driverLoc, deliveryLoc);
        }

        // Pan to driver location
        console.log("Panning map to driver location");
        trackingMap.panTo([driverLoc.lat, driverLoc.lng]);
    } else {
        console.log("❌ Map not initialized, cannot update driver location");
    }
}

// Helper function to calculate distance between two points (Haversine formula)
function calculateDistance(lat1, lon1, lat2, lon2) {
    const R = 6371; // Radius of Earth in km
    const dLat = (lat2 - lat1) * Math.PI / 180;
    const dLon = (lon2 - lon1) * Math.PI / 180;
    const a = Math.sin(dLat/2) * Math.sin(dLat/2) +
              Math.cos(lat1 * Math.PI / 180) * Math.cos(lat2 * Math.PI / 180) *
              Math.sin(dLon/2) * Math.sin(dLon/2);
    const c = 2 * Math.atan2(Math.sqrt(a), Math.sqrt(1-a));
    return R * c; // Distance in km
}

function getStatusBasedInfo(order) {
    const status = (order.status || "").toUpperCase();

    if (status === "PLACED" || status === "PENDING") {
        return `
            <div style="background: linear-gradient(135deg, #fff3cd 0%, #ffeaa7 100%); padding: 1.5rem; border-radius: 8px; border-left: 4px solid #f39c12;">
                <h4 style="margin: 0 0 0.5rem 0; font-size: 1rem; color: #856404;">📋 Order Received</h4>
                <p style="color: #856404; margin: 0; font-size: 0.9rem;">
                    We've received your order and are processing it. You'll be notified once the restaurant confirms!
                </p>
            </div>
        `;
    } else if (status === "CONFIRMED") {
        return `
            <div style="background: linear-gradient(135deg, #d1ecf1 0%, #bee5eb 100%); padding: 1.5rem; border-radius: 8px; border-left: 4px solid #17a2b8;">
                <h4 style="margin: 0 0 0.5rem 0; font-size: 1rem; color: #0c5460;">✅ Restaurant Confirmed</h4>
                <p style="color: #0c5460; margin: 0; font-size: 0.9rem;">
                    The restaurant has confirmed your order. We're finding the best driver for you!
                </p>
            </div>
        `;
    } else if (status === "PREPARING") {
        return `
            <div style="background: linear-gradient(135deg, #d4edda 0%, #c3e6cb 100%); padding: 1.5rem; border-radius: 8px; border-left: 4px solid #28a745;">
                <h4 style="margin: 0 0 0.5rem 0; font-size: 1rem; color: #155724;">👨‍🍳 Preparing Your Food</h4>
                <p style="color: #155724; margin: 0 0 0.75rem 0; font-size: 0.9rem;">
                    The restaurant is preparing your delicious meal. A driver will be assigned shortly!
                </p>
                <div id="tracking-eta" style="font-size: 1.1rem; font-weight: 600; color: #155724; margin-bottom: 0.5rem;"></div>
                <div id="driver-location"></div>
            </div>
        `;
    } else if (status === "DRIVER_ASSIGNED") {
        return `
            <div style="background: linear-gradient(135deg, #cfe2ff 0%, #b8daff 100%); padding: 1.5rem; border-radius: 8px; border-left: 4px solid #0d6efd;">
                <h4 style="margin: 0 0 0.5rem 0; font-size: 1rem; color: #052c65;">🚗 Driver Assigned</h4>
                <p style="color: #052c65; margin: 0 0 0.75rem 0; font-size: 0.9rem;">
                    Your driver is on the way to the restaurant to pick up your order!
                </p>
                <div id="tracking-eta" style="font-size: 1.1rem; font-weight: 600; color: #052c65; margin-bottom: 0.5rem;"></div>
                <div id="driver-location"></div>
            </div>
        `;
    } else if (status === "PICKED_UP") {
        return `
            <div style="background: linear-gradient(135deg, #e7f1ff 0%, #cfe2ff 100%); padding: 1.5rem; border-radius: 8px; border-left: 4px solid #0d6efd;">
                <h4 style="margin: 0 0 0.5rem 0; font-size: 1rem; color: #004085;">📦 Order Picked Up</h4>
                <p style="color: #004085; margin: 0 0 0.75rem 0; font-size: 0.9rem;">
                    Your driver has picked up your order from the restaurant and will be on the way shortly!
                </p>
                <div id="tracking-eta" style="font-size: 1.1rem; font-weight: 600; color: #004085; margin-bottom: 0.5rem;"></div>
                <div id="driver-location"></div>
            </div>
        `;
    } else if (status === "DELIVERING") {
        return `
            <div style="background: linear-gradient(135deg, #cce5ff 0%, #b8daff 100%); padding: 1.5rem; border-radius: 8px; border-left: 4px solid #004085;">
                <h4 style="margin: 0 0 0.5rem 0; font-size: 1rem; color: #004085;">🚗 Out for Delivery!</h4>
                <p style="color: #004085; margin: 0 0 0.75rem 0; font-size: 0.9rem;">
                    Your driver is on the way to your location with your food!
                </p>
                <div id="tracking-eta" style="font-size: 1.1rem; font-weight: 600; color: #004085; margin-bottom: 0.5rem;"></div>
                <div id="driver-location"></div>
            </div>
        `;
    } else if (order.delivery_id && (status === "CONFIRMED" || status === "PLACED" || status === "PENDING" || status === "PREPARING")) {
        // Show tracking info for any order with a delivery_id, even if not DELIVERING yet
        return `
            <div style="background: linear-gradient(135deg, #cce5ff 0%, #b8daff 100%); padding: 1.5rem; border-radius: 8px; border-left: 4px solid #004085;">
                <h4 style="margin: 0 0 0.5rem 0; font-size: 1rem; color: #004085;">📍 Live Tracking Available</h4>
                <p style="color: #004085; margin: 0.25rem 0 0.75rem 0; font-size: 0.9rem;">
                    <strong>Delivery ID:</strong> ${order.delivery_id.substring(0, 8)}
                </p>
                <div id="tracking-eta" style="font-size: 1.1rem; font-weight: 600; color: #004085; margin-bottom: 0.5rem;"></div>
                <div id="driver-location"></div>
            </div>
        `;
    } else if (status === "COMPLETED" || status === "DELIVERED") {
        return `
            <div style="background: linear-gradient(135deg, #d4edda 0%, #c3e6cb 100%); padding: 1.5rem; border-radius: 8px; border-left: 4px solid #28a745; text-align: center;">
                <h4 style="margin: 0 0 0.5rem 0; font-size: 1.2rem; color: #155724;">🎉 Delivered!</h4>
                <p style="color: #155724; margin: 0; font-size: 0.9rem;">
                    Your order has been delivered. Enjoy your meal!
                </p>
            </div>
        `;
    } else if (status === "FAILED") {
        return `
            <div style="background: linear-gradient(135deg, #f8d7da 0%, #f5c6cb 100%); padding: 1.5rem; border-radius: 8px; border-left: 4px solid #dc3545; text-align: center;">
                <h4 style="margin: 0 0 0.5rem 0; font-size: 1.2rem; color: #721c24;">❌ Order Failed</h4>
                <p style="color: #721c24; margin: 0; font-size: 0.9rem;">
                    Unfortunately, this order could not be completed. ${order.failure_reason || 'No available drivers were found to deliver your order.'} Please try placing a new order.
                </p>
            </div>
        `;
    } else if (status === "CANCELLED") {
        return `
            <div style="background: linear-gradient(135deg, #f8d7da 0%, #f5c6cb 100%); padding: 1.5rem; border-radius: 8px; border-left: 4px solid #dc3545; text-align: center;">
                <h4 style="margin: 0 0 0.5rem 0; font-size: 1.2rem; color: #721c24;">🚫 Order Cancelled</h4>
                <p style="color: #721c24; margin: 0; font-size: 0.9rem;">
                    This order has been cancelled. ${order.cancellation_reason || 'You can place a new order anytime.'}
                </p>
            </div>
        `;
    } else {
        return `
            <div style="background: var(--bg); padding: 1rem; border-radius: 6px; text-align: center;">
                <p style="color: var(--text-light); font-style: italic; margin: 0;">⏳ Processing your order...</p>
            </div>
        `;
    }
}


function showPage(pageId) {
    // Track previous page for back navigation (unless going to restaurant-detail)
    const currentPage = document.querySelector(".page.active")?.id?.replace("page-", "");
    if (currentPage && currentPage !== pageId && pageId !== "restaurant-detail") {
        previousPage = currentPage;
    }

    document.querySelectorAll(".page").forEach((p) => p.classList.remove("active"));
    const page = document.getElementById(`page-${pageId}`);
    if (page) page.classList.add("active");

    // Update browser history
    if (history.state?.page !== pageId) {
        history.pushState({ page: pageId, previousPage: previousPage }, '', `#${pageId}`);
    }

    // Load driver dashboard content if on driver page
    if (pageId === "driver-dashboard") {
        loadDriverDashboard();
    } else if (pageId === "driver-deliveries") {
        loadDriverDeliveries();
    } else if (pageId === "profile") {
        loadProfile();
    }

    if (pageId === "restaurants") loadRestaurants();
    if (pageId === "orders") loadOrders();
    if (pageId === "cart") renderCart();
    if (pageId === "tracking") {
        // If navigating directly to tracking without order ID, redirect to orders
        const currentContent = document.getElementById("tracking-info")?.innerHTML || '';
        if (!currentContent || currentContent.includes('Loading')) {
            showPage("orders");
        }
    }
}

// Handle browser back/forward buttons
window.addEventListener('popstate', (event) => {
    if (event.state && event.state.page) {
        const pageId = event.state.page;
        // Restore previousPage from history state
        if (event.state.previousPage) {
            previousPage = event.state.previousPage;
        }

        document.querySelectorAll(".page").forEach((p) => p.classList.remove("active"));
        const page = document.getElementById(`page-${pageId}`);
        if (page) page.classList.add("active");

        if (pageId === "restaurants") loadRestaurants();
        if (pageId === "orders") loadOrders();
        if (pageId === "cart") renderCart();
        if (pageId === "tracking") {
            // If navigating back to tracking without order ID, redirect to orders
            const currentContent = document.getElementById("tracking-info")?.innerHTML || '';
            if (!currentContent || currentContent.includes('Loading')) {
                showPage("orders");
            }
        }
    }
});


// ── API Helpers ──
async function apiCall(endpoint, method = "GET", body = null) {
    if (!API_BASE) {
        console.error("API_BASE not configured. Check config.js");
        throw new Error("API not configured");
    }

    const headers = { "Content-Type": "application/json" };
    if (authToken) headers["Authorization"] = `Bearer ${authToken}`;

    const opts = { method, headers };
    if (body) opts.body = JSON.stringify(body);

    console.log(`API Call: ${method} ${API_BASE}${endpoint}`);
    const res = await fetch(`${API_BASE}${endpoint}`, opts);

    const data = await res.json();

    if (!res.ok) {
        console.error(`API Error (${res.status}):`, data);
        // Create error with the API message
        const error = new Error(data.message || `API Error: ${res.status}`);
        error.status = res.status;
        error.data = data;
        throw error;
    }

    return data;
}


async function handleLogin(e) {
    e.preventDefault();
    const email = document.getElementById("login-email").value;
    const password = document.getElementById("login-password").value;

    try {
        const data = await apiCall("/auth/login", "POST", { email, password });
        if (data.access_token || data.id_token) {
            authToken = data.id_token || data.access_token;

            // Decode JWT to get user info and role
            const userInfo = parseJWT(authToken);
            currentUser = {
                email,
                sub: userInfo.sub,
                role: userInfo['custom:role'] || 'customer'
            };

            console.log("User logged in:", currentUser);
            onLoginSuccess();
        } else {
            showToast(data.message || "Login failed", "error");
        }
    } catch (err) {
        console.error("Login error:", err);
        // Check if it's an email not confirmed error
        if (err.message && err.message.includes("403")) {
            showToast("Please verify your email first. Check your inbox for the verification code.", "error");
            pendingVerificationEmail = email;
            document.getElementById("verify-email-display").textContent = email;
            setTimeout(() => showPage("verify"), 2000);
        } else {
            showToast("Login failed - check your credentials", "error");
        }
    }
}

async function handleSignup(e) {
    e.preventDefault();
    const name = document.getElementById("signup-name").value;
    const email = document.getElementById("signup-email").value;
    const password = document.getElementById("signup-password").value;
    const role = document.getElementById("signup-role").value;

    try {
        const data = await apiCall("/auth/signup", "POST", { full_name: name, email, password, role });
        if (data.user_id) {
            pendingVerificationEmail = email;
            document.getElementById("verify-email-display").textContent = email;
            showToast("Account created! Please check your email for verification code.", "success");
            showPage("verify");
        } else {
            showToast(data.message || "Signup failed", "error");
        }
    } catch (err) {
        console.error("Signup error:", err);
        showToast(err.message || "Signup failed - API not connected", "error");
    }
}

async function handleVerify(e) {
    e.preventDefault();
    const code = document.getElementById("verify-code").value;

    if (!pendingVerificationEmail) {
        showToast("No pending verification", "error");
        showPage("signup");
        return;
    }

    try {
        const data = await apiCall("/auth/verify", "POST", { email: pendingVerificationEmail, code });
        if (data.message) {
            showToast("Email verified! You can now login.", "success");
            pendingVerificationEmail = null;
            showPage("login");
        } else {
            showToast(data.message || "Verification failed", "error");
        }
    } catch (err) {
        console.error("Verify error:", err);
        showToast("Verification failed - please check the code", "error");
    }
}

async function onLoginSuccess() {
    document.getElementById("nav-auth").classList.add("hidden");
    document.getElementById("nav-user").classList.remove("hidden");
    document.getElementById("user-name").textContent = currentUser?.name || currentUser?.email || "User";

    // Store auth token in localStorage for persistence
    localStorage.setItem("authToken", authToken);
    localStorage.setItem("currentUser", JSON.stringify(currentUser));

    // Connect WebSocket for real-time updates
    connectWebSocket();

    // Show different interface based on role
    if (currentUser?.role === 'driver') {
        showToast("Welcome Driver!", "success");
        showDriverInterface();
        showPage("driver-dashboard");
    } else {
        // Load cart from backend (only for customers)
        await loadCartFromServer();
        showToast("Welcome back!", "success");
        showPage("home");
    }
}

async function loadCartFromServer() {
    if (!authToken) return;

    try {
        const data = await apiCall("/cart", "GET");
        const serverCart = data.cart || {};
        const items = serverCart.items || [];

        // Convert server cart format to frontend format
        cart.items = items.map(item => ({
            item_id: item.menu_item_id,
            restaurant_id: item.restaurant_id,
            name: item.name,
            price: parseFloat((item.unit_price_cents || 0) / 100),
            quantity: parseInt(item.quantity || 0),
            line_id: item.line_id,
            selected: true // Default to selected
        }));

        updateCartCount();
    } catch (err) {
        console.error("Failed to load cart:", err);
    }
}

async function syncCartToServer(restaurantId, itemId, name, price, quantity) {
    if (!authToken) return;

    try {
        await apiCall("/cart", "POST", {
            restaurant_id: restaurantId,
            menu_item_id: itemId,
            name: name,
            unit_price_cents: Math.round(price * 100),
            quantity: quantity
        });
    } catch (err) {
        console.error("Failed to sync cart:", err);
        showToast("Cart sync failed, but item added locally", "error");
    }
}

async function updateCartQuantityOnServer(lineId, quantity) {
    if (!authToken) return;

    try {
        await apiCall("/cart", "PUT", {
            line_id: lineId,
            quantity: quantity
        });
    } catch (err) {
        console.error("Failed to update cart quantity:", err);
    }
}

async function logout() {
    // Disconnect WebSocket before clearing token
    disconnectWebSocket();

    // Don't clear cart on backend - it should persist!
    // Just clear local state
    currentUser = null;
    authToken = null;
    cart = { items: [] };

    // Clear localStorage
    localStorage.removeItem("authToken");
    localStorage.removeItem("currentUser");

    updateCartCount();
    document.getElementById("nav-auth").classList.remove("hidden");
    document.getElementById("nav-user").classList.add("hidden");
    showPage("home");
    showToast("Logged out successfully", "success");
}


async function loadRestaurants() {
    const container = document.getElementById("restaurants-list");
    try {
        const data = await apiCall("/restaurants");
        let restaurants = data.restaurants || data || [];

        // Apply filters
        const cuisineFilter = document.getElementById("filter-cuisine")?.value.toLowerCase();
        const sortBy = document.getElementById("filter-sort")?.value;

        if (cuisineFilter) {
            restaurants = restaurants.filter(r => r.cuisine.toLowerCase() === cuisineFilter);
        }

        // Apply sorting
        if (sortBy === "rating") {
            restaurants.sort((a, b) => (b.avg_rating || 0) - (a.avg_rating || 0));
        } else if (sortBy === "name") {
            restaurants.sort((a, b) => a.name.localeCompare(b.name));
        }

        if (restaurants.length === 0) {
            container.innerHTML = '<div class="empty-state"><p>No restaurants found</p></div>';
        } else {
            container.innerHTML = restaurants.map(renderRestaurantCard).join("");
        }
    } catch (err) {
        console.error("Failed to load restaurants:", err);
        container.innerHTML = '<div class="empty-state"><p>Unable to load restaurants, please try again</p></div>';
    }
}


async function loadPopularRestaurants() {
    const container = document.getElementById("popular-restaurants");
    try {
        const data = await apiCall("/restaurants");
        const restaurants = data.restaurants || data || [];
        if (restaurants.length === 0) {
            container.innerHTML = '<div class="empty-state"><p>No restaurants found</p></div>';
        } else {
            container.innerHTML = restaurants.slice(0, 6).map(renderRestaurantCard).join("");
        }
    } catch (err) {
        console.error("Failed to load popular restaurants:", err);
        container.innerHTML = '<div class="empty-state"><p>Unable to load restaurants, please try again</p></div>';
    }
}


function renderRestaurantCard(r) {
    return `
        <div class="restaurant-card" onclick="viewRestaurant('${r.restaurant_id}')">
            <img src="${r.image_url || 'https://images.unsplash.com/photo-1517248135467-4c7edcad34c4?w=400'}" alt="${r.name}">
            <div class="card-body">
                <h3>${r.name}</h3>
                <div class="card-meta">
                    <span>${r.cuisine || 'Various'}</span>
                    <span class="rating">★ ${r.avg_rating || '4.5'}</span>
                    <span>${r.delivery_time || '30-45'} min</span>
                </div>
            </div>
        </div>
    `;
}


async function viewRestaurant(id) {
    // Store current page as previous before navigating
    const currentPage = document.querySelector(".page.active")?.id?.replace("page-", "");
    if (currentPage && currentPage !== "restaurant-detail") {
        previousPage = currentPage;
    }

    showPage("restaurant-detail");
    const header = document.getElementById("restaurant-header");
    const menuList = document.getElementById("menu-list");

    // Clear previous content immediately to avoid flash
    header.innerHTML = '<div style="padding: 1rem;">Loading...</div>';
    menuList.innerHTML = '<div style="padding: 2rem; text-align: center;">Loading menu...</div>';

    try {
        const data = await apiCall(`/restaurants/${id}`);
        const restaurant = data.restaurant || data;
        header.innerHTML = `
            <h2>${restaurant.name}</h2>
            <div class="meta">
                <span>${restaurant.cuisine || 'Various'}</span>
                <span class="rating">★ ${restaurant.avg_rating || '4.5'}</span>
                <span>${restaurant.address || ''}</span>
            </div>
        `;
        const menuData = await apiCall(`/restaurants/${id}/menu`);
        const items = menuData.items || menuData || [];
        menuList.innerHTML = items.map((item) => renderMenuItem(item, id)).join("");
    } catch (err) {
        console.error("Error loading restaurant/menu:", err);
        const mock = getMockMenu();
        header.innerHTML = `<h2>Sample Restaurant</h2><div class="meta"><span>Various</span><span class="rating">★ 4.5</span></div>`;
        menuList.innerHTML = mock.map((item) => renderMenuItem(item, id)).join("");
    }
}

function goBack() {
    // Use browser back if available, otherwise go to previous page
    if (window.history.length > 1) {
        window.history.back();
    } else {
        showPage(previousPage || "home");
    }
}


function renderMenuItem(item, restaurantId) {
    const imageUrl = item.image_url || 'https://images.unsplash.com/photo-1546069901-ba9599a7e63c?w=200';
    const price = parseFloat(item.price || 0);
    return `
        <div class="menu-item">
            <img src="${imageUrl}" alt="${item.name}" style="width: 100px; height: 100px; object-fit: cover; border-radius: 8px; margin-right: 1rem;" onerror="console.error('Failed to load image:', this.src); this.src='https://images.unsplash.com/photo-1546069901-ba9599a7e63c?w=200';">
            <div class="menu-item-info">
                <h4>${item.name}</h4>
                <p>${item.description || ''}</p>
            </div>
            <div class="menu-item-actions">
                <span class="menu-item-price">$${price.toFixed(2)}</span>
                <button class="btn btn-primary btn-small" onclick="addToCart('${restaurantId}', '${item.item_id}', '${item.name}', ${price})">Add</button>
            </div>
        </div>
    `;
}


function handleSearch(e) {
    if (e.key === "Enter") searchRestaurants();
}

async function searchRestaurants() {
    const query = document.getElementById("search-input").value;
    if (!query) return;
    showPage("restaurants");
    const container = document.getElementById("restaurants-list");
    try {
        const data = await apiCall(`/search?q=${encodeURIComponent(query)}`);
        const results = data.restaurants || data || [];
        container.innerHTML = results.length
            ? results.map(renderRestaurantCard).join("")
            : '<div class="empty-state"><p>No restaurants found</p></div>';
    } catch {
        container.innerHTML = '<div class="empty-state"><p>Search not available - API not connected</p></div>';
    }
}

function filterRestaurants() {
    loadRestaurants();
}


async function addToCart(restaurantId, itemId, name, price) {
    const existing = cart.items.find((i) => i.item_id === itemId && i.restaurant_id === restaurantId);

    if (existing) {
        existing.quantity = parseInt(existing.quantity) + 1;
        // Update on server if logged in
        if (authToken && existing.line_id) {
            await updateCartQuantityOnServer(existing.line_id, existing.quantity);
        }
    } else {
        const newItem = {
            item_id: itemId,
            restaurant_id: restaurantId,
            name,
            price: parseFloat(price),
            quantity: 1,
            selected: true // Default to selected
        };
        cart.items.push(newItem);

        // Sync to server if logged in
        if (authToken) {
            await syncCartToServer(restaurantId, itemId, name, price, 1);
            // Reload cart to get line_id
            await loadCartFromServer();
        }
    }

    updateCartCount();
    showToast(`${name} added to cart`, "success");
}


function updateCartCount() {
    const count = cart.items.reduce((sum, i) => sum + parseInt(i.quantity || 0), 0);
    document.getElementById("cart-count").textContent = count;
}


async function updateQuantity(itemId, restaurantId, delta) {
    const item = cart.items.find((i) => i.item_id === itemId && i.restaurant_id === restaurantId);
    if (!item) return;

    item.quantity = parseInt(item.quantity) + delta;

    // Update on server if logged in
    if (authToken && item.line_id) {
        await updateCartQuantityOnServer(item.line_id, item.quantity);
    }

    if (item.quantity <= 0) {
        cart.items = cart.items.filter((i) => !(i.item_id === itemId && i.restaurant_id === restaurantId));
    }

    if (cart.items.length === 0) {
        // Clear cart on server
        if (authToken) {
            try {
                await apiCall("/cart", "DELETE");
            } catch (err) {
                console.error("Failed to clear cart:", err);
            }
        }
    }
    updateCartCount();
    renderCart();
}

function toggleItemSelection(itemId, restaurantId) {
    const item = cart.items.find((i) => i.item_id === itemId && i.restaurant_id === restaurantId);
    if (item) {
        item.selected = !item.selected;
        renderCart();
    }
}


function renderCart() {
    const container = document.getElementById("cart-items");
    const summary = document.getElementById("cart-summary");
    const empty = document.getElementById("cart-empty");

    if (cart.items.length === 0) {
        container.innerHTML = "";
        summary.classList.add("hidden");
        empty.classList.remove("hidden");
        return;
    }

    empty.classList.add("hidden");
    summary.classList.remove("hidden");

    // Group items by restaurant
    const itemsByRestaurant = cart.items.reduce((groups, item) => {
        if (!groups[item.restaurant_id]) {
            groups[item.restaurant_id] = [];
        }
        groups[item.restaurant_id].push(item);
        return groups;
    }, {});

    container.innerHTML = Object.entries(itemsByRestaurant)
        .map(([restaurantId, items]) => `
            <div class="restaurant-cart-group">
                <h3 style="margin-bottom: 1rem; color: var(--primary-color);">Restaurant: ${restaurantId}</h3>
                ${items.map(item => `
                    <div class="cart-item" style="opacity: ${item.selected ? '1' : '0.6'};">
                        <input
                            type="checkbox"
                            ${item.selected ? 'checked' : ''}
                            onchange="toggleItemSelection('${item.item_id}', '${item.restaurant_id}')"
                            style="margin-right: 0.5rem; cursor: pointer; width: 18px; height: 18px;"
                        >
                        <div class="cart-item-info">
                            <strong>${item.name}</strong>
                            <div>$${item.price.toFixed(2)} each</div>
                        </div>
                        <div class="cart-item-qty">
                            <button onclick="updateQuantity('${item.item_id}', '${item.restaurant_id}', -1)">−</button>
                            <span>${item.quantity}</span>
                            <button onclick="updateQuantity('${item.item_id}', '${item.restaurant_id}', 1)">+</button>
                        </div>
                        <strong>$${(item.price * item.quantity).toFixed(2)}</strong>
                    </div>
                `).join('')}
            </div>
        `).join('');

    // Calculate total for selected items only
    const total = cart.items
        .filter(i => i.selected)
        .reduce((sum, i) => sum + i.price * i.quantity, 0);
    document.getElementById("cart-total-price").textContent = `$${total.toFixed(2)}`;
}


async function placeOrder() {
    if (!authToken) {
        showToast("Please login to place an order", "error");
        showPage("login");
        return;
    }

    const selectedItems = cart.items.filter(i => i.selected);

    if (selectedItems.length === 0) {
        showToast("Please select at least one item to order", "error");
        return;
    }

    // Group selected items by restaurant
    const itemsByRestaurant = selectedItems.reduce((groups, item) => {
        if (!groups[item.restaurant_id]) {
            groups[item.restaurant_id] = [];
        }
        groups[item.restaurant_id].push(item);
        return groups;
    }, {});

    const promoCode = document.getElementById("promo-code").value;
    let successCount = 0;
    let failCount = 0;

    try {
        // Place separate orders for each restaurant
        for (const [restaurantId, items] of Object.entries(itemsByRestaurant)) {
            try {
                // First, update the cart on the server to only contain items for this restaurant
                // This is a workaround since the backend reads from cart
                const data = await apiCall("/orders", "POST", {
                    restaurant_id: restaurantId,
                    notes: promoCode ? `Promo: ${promoCode}` : undefined,
                });

                if (data.order && data.order.order_id) {
                    successCount++;
                    // Remove ordered items from local cart
                    cart.items = cart.items.filter(i =>
                        !(i.selected && i.restaurant_id === restaurantId)
                    );
                } else {
                    failCount++;
                }
            } catch (err) {
                console.error(`Order failed for restaurant ${restaurantId}:`, err);
                failCount++;
            }
        }

        // Sync remaining items back to server
        // The backend clears the cart after placing an order, so we need to re-add remaining items
        if (successCount > 0 && cart.items.length > 0) {
            try {
                // Re-add remaining items to cart on server
                for (const item of cart.items) {
                    await syncCartToServer(
                        item.restaurant_id,
                        item.item_id,
                        item.name,
                        item.price,
                        item.quantity
                    );
                }
                // Reload to get line_ids
                await loadCartFromServer();
            } catch (err) {
                console.error("Failed to restore remaining cart items:", err);
            }
        } else if (successCount > 0 && cart.items.length === 0) {
            // All items ordered, clear cart on server
            try {
                await apiCall("/cart", "DELETE");
            } catch (err) {
                console.error("Failed to clear cart:", err);
            }
        }

        updateCartCount();
        renderCart();

        if (successCount > 0 && failCount === 0) {
            showToast(`${successCount} order(s) placed successfully!`, "success");
            showPage("orders");
        } else if (successCount > 0) {
            showToast(`${successCount} order(s) placed, ${failCount} failed`, "error");
        } else {
            showToast("All orders failed - please try again", "error");
        }
    } catch (err) {
        console.error("Order error:", err);
        showToast(err.message || "Order failed - please try again", "error");
    }
}


async function loadOrders() {
    const container = document.getElementById("orders-list");
    if (!authToken) {
        container.innerHTML = '<div class="empty-state"><p>Please login to view orders</p></div>';
        return;
    }
    try {
        const data = await apiCall("/orders");
        const orders = data.orders || data || [];

        // Debug: log the orders to see actual status values
        console.log("Loaded orders:", orders);
        if (orders.length > 0) {
            console.log("First order status:", orders[0].status, "Type:", typeof orders[0].status);
        }

        container.innerHTML = orders.length
            ? orders.map(renderOrderCard).join("")
            : '<div class="empty-state"><p>No orders yet</p></div>';
    } catch (err) {
        console.error("Failed to load orders:", err);
        container.innerHTML = '<div class="empty-state"><p>Could not load orders</p></div>';
    }
}


function renderOrderCard(order) {
    const status = (order.status || "pending").toLowerCase();
    const statusClass = `status-${status}`;

    // Format created_at date
    let dateStr = order.created_at || '';
    try {
        if (dateStr) {
            const date = new Date(dateStr);
            dateStr = date.toLocaleString();
        }
    } catch (e) {
        // Keep original string if parsing fails
    }

    return `
        <div class="order-card clickable" data-order-id="${order.order_id}" onclick="trackOrder('${order.order_id}')">
            <div class="order-header">
                <div>
                    <strong>Order #${(order.order_id || "").substring(0, 8)}</strong>
                    <div style="color: var(--text-light); font-size: 0.85rem">${dateStr}</div>
                </div>
                <span class="order-status ${statusClass}">${(order.status || "pending").toUpperCase()}</span>
            </div>
            <div style="margin: 0.75rem 0;">
                ${(order.items || []).map((i) => `<span>${i.name} x${i.quantity}</span>`).join(", ")}
            </div>
            <div style="display: flex; justify-content: space-between; align-items: center;">
                <strong style="font-size: 1.1rem;">$${parseFloat(order.total || 0).toFixed(2)}</strong>
                <span style="color: var(--text-light); font-size: 0.85rem;">Click to track →</span>
            </div>
        </div>
    `;
}


async function trackOrder(orderId) {
    showPage("tracking");

    // Store the order ID in localStorage for persistence across refreshes
    localStorage.setItem("currentTrackedOrderId", orderId);

    const info = document.getElementById("tracking-info");
    const timeline = document.getElementById("tracking-status");

    // Clean up previous map and order data
    if (trackingMap) {
        trackingMap.remove();
        trackingMap = null;
        driverMarker = null;
        restaurantMarker = null;
        destinationMarker = null;
        routeLine = null;
        currentOrderData = null;
    }
    currentTrackedOrder = null;
    document.getElementById("tracking-map").style.display = "none";
    const mapLegend = document.getElementById("map-legend");
    if (mapLegend) mapLegend.style.display = "none";

    // Show loading state
    info.innerHTML = '<div style="padding: 2rem; text-align: center;">Loading order details...</div>';
    timeline.innerHTML = '';

    // Fetch order details from API
    try {
        const data = await apiCall(`/orders/${orderId}`);
        const order = data.order;

        // Store order globally for status updates
        currentTrackedOrder = order;

        console.log("Order data:", order); // Debug

        // Calculate total from items
        const total = (order.items || []).reduce((sum, item) =>
            sum + (parseFloat(item.unit_price_cents || 0) / 100) * (item.quantity || 0), 0);

        // Format date
        let dateStr = order.created_at || '';
        try {
            if (dateStr) {
                const date = new Date(dateStr);
                dateStr = date.toLocaleString();
            }
        } catch (e) {
            // Keep original string if parsing fails
        }

        const status = (order.status || "pending").toLowerCase();
        const statusClass = `status-${status}`;

        // Render detailed order info
        info.innerHTML = `
            <h2 style="margin-bottom: 1.5rem;">Order Details</h2>
            <div style="background: var(--white); padding: 2rem; border-radius: var(--radius); box-shadow: var(--shadow); margin-bottom: 2rem;">
                <div style="display: flex; justify-content: space-between; align-items: flex-start; margin-bottom: 1.5rem; padding-bottom: 1rem; border-bottom: 2px solid var(--border);">
                    <div>
                        <h3 style="margin: 0; font-size: 1.5rem;">Order #${orderId.substring(0, 8)}</h3>
                        <p style="color: var(--text-light); font-size: 0.9rem; margin: 0.5rem 0 0 0;">
                            <strong>Placed:</strong> ${dateStr}
                        </p>
                        ${order.restaurant_id ? `
                            <p style="color: var(--text-light); font-size: 0.9rem; margin: 0.25rem 0 0 0;">
                                <strong>Restaurant ID:</strong> ${order.restaurant_id.substring(0, 8)}
                            </p>
                        ` : ''}
                    </div>
                    <span class="order-status ${statusClass}" style="font-size: 1rem; padding: 0.5rem 1rem;">
                        ${(order.status || "pending").toUpperCase()}
                    </span>
                </div>

                <div style="margin-bottom: 1.5rem;">
                    <h4 style="margin-bottom: 1rem; font-size: 1.1rem; color: var(--text);">Order Items</h4>
                    ${(order.items || []).map(item => `
                        <div style="display: flex; justify-content: space-between; align-items: center; padding: 0.75rem; background: var(--bg); border-radius: 6px; margin-bottom: 0.5rem;">
                            <div>
                                <strong>${item.name}</strong>
                                <span style="color: var(--text-light); margin-left: 0.5rem;">x${item.quantity}</span>
                            </div>
                            <span style="font-weight: 600;">$${((item.unit_price_cents || 0) / 100 * item.quantity).toFixed(2)}</span>
                        </div>
                    `).join('')}
                    <div style="display: flex; justify-content: space-between; align-items: center; padding: 1rem 0.75rem; margin-top: 1rem; border-top: 2px solid var(--border); font-size: 1.2rem;">
                        <strong>Total:</strong>
                        <strong style="color: var(--primary);">$${parseFloat(order.total || total).toFixed(2)}</strong>
                    </div>
                </div>

                <div id="status-based-info">
                    ${getStatusBasedInfo(order)}
                </div>
            </div>
        `;

        // Render timeline based on current status
        updateTrackingTimeline(orderId, order.status);

        // Initialize map for all orders
        // Driver location will be updated via WebSocket
        const mapData = {
            restaurant_location: order.restaurant_location || "37.7749,-122.4194", // SF default
            delivery_address: order.delivery_address || "37.7849,-122.4094", // SF default
            driver_location: order.driver_location || null, // Will be updated via WebSocket
            status: order.status
        };

        // HYBRID APPROACH: Determine driver location based on order status
        const orderStatus = (order.status || "").toLowerCase();
        console.log(`🚗 [INITIAL LOAD] Order status: ${orderStatus.toUpperCase()}`);

        if (order.delivery_id && (orderStatus === "driver_assigned" || orderStatus === "picked_up" || orderStatus === "delivering" || orderStatus === "delivered")) {
            const restaurantLoc = parseLocation(order.restaurant_location, { lat: 37.7849, lng: -122.4094 });
            const deliveryLoc = parseLocation(order.delivery_address, { lat: 37.7749, lng: -122.4194 });

            if (orderStatus === "picked_up" || orderStatus === "delivering") {
                // LOGICAL: Driver at restaurant (either just picked up or delivering)
                console.log(`📦 [${orderStatus.toUpperCase()}] Driver position: Restaurant`);
                mapData.driver_location = restaurantLoc;

            } else if (orderStatus === "delivered") {
                // LOGICAL: Driver delivered at customer location
                console.log("🏠 [DELIVERED] Driver position: Customer location");
                mapData.driver_location = deliveryLoc;

            } else if (orderStatus === "driver_assigned") {
                // LOGICAL: Driver at restaurant (assigned, waiting to pick up)
                console.log("📍 [DRIVER_ASSIGNED] Driver position: Restaurant");
                mapData.driver_location = restaurantLoc;
            }
        } else {
            console.log("No delivery_id or status doesn't require driver marker");
        }

        console.log("Final mapData:", mapData);
        initializeTrackingMap(mapData);

        // Subscribe to WebSocket updates for this order
        if (order.delivery_id) {
            // Use the full delivery_id for WebSocket subscription
            const deliveryId = order.delivery_id;
            console.log(`Auto-subscribing to delivery: ${deliveryId}`);
            subscribeToDelivery(deliveryId);
        } else {
            console.log("Order doesn't have delivery_id yet - map shows restaurant and destination");
        }
    } catch (err) {
        console.error("Failed to load order:", err);
        info.innerHTML = `
            <div style="background: var(--white); padding: 2rem; border-radius: var(--radius); text-align: center;">
                <p style="color: var(--primary); margin-bottom: 1rem;">❌ Failed to load order details</p>
                <p style="color: var(--text-light); font-size: 0.9rem;">${err.message || 'Unknown error'}</p>
                <button class="btn btn-primary" onclick="showPage('orders')" style="margin-top: 1rem;">Back to Orders</button>
            </div>
        `;
        timeline.innerHTML = '';
    }
}

function subscribeToDelivery(deliveryId) {
    // If WebSocket isn't connected, connect it first
    if (!wsConnection || wsConnection.readyState !== WebSocket.OPEN) {
        console.log("WebSocket not connected, connecting now...");

        // Connect WebSocket if we have auth token
        if (authToken) {
            connectWebSocket();

            // Wait for connection to establish, then subscribe
            const checkConnection = setInterval(() => {
                if (wsConnection && wsConnection.readyState === WebSocket.OPEN) {
                    clearInterval(checkConnection);
                    sendWebSocketMessage("subscribe", { delivery_id: deliveryId });
                    console.log(`Subscribed to delivery ${deliveryId}`);
                    showToast("Connected to real-time tracking", "success");
                }
            }, 100);

            // Stop checking after 5 seconds
            setTimeout(() => clearInterval(checkConnection), 5000);
        } else {
            console.error("Cannot subscribe: No auth token");
            showToast("Real-time tracking unavailable - please login", "error");
        }
        return;
    }

    sendWebSocketMessage("subscribe", { delivery_id: deliveryId });
    console.log(`Subscribed to delivery ${deliveryId}`);
}

function updateTrackingTimeline(orderId, status) {
    const timeline = document.getElementById("tracking-status");
    if (!timeline) return;

    // Normalize status to uppercase
    const normalizedStatus = (status || "").toUpperCase();

    // Map status to timeline steps with descriptions
    const steps = [
        {
            key: "PLACED",
            label: "Order Placed",
            description: "Your order has been received",
            icon: "📋"
        },
        {
            key: "CONFIRMED",
            label: "Restaurant Confirmed",
            description: "Restaurant accepted your order",
            icon: "✅"
        },
        {
            key: "PREPARING",
            label: "Preparing Food",
            description: "Your food is being prepared",
            icon: "👨‍🍳"
        },
        {
            key: "DRIVER_ASSIGNED",
            label: "Driver En Route",
            description: "Driver heading to restaurant",
            icon: "🚗"
        },
        {
            key: "PICKED_UP",
            label: "Picked Up",
            description: "Driver picked up your order",
            icon: "📦"
        },
        {
            key: "DELIVERING",
            label: "Out for Delivery",
            description: "Driver is on the way to you",
            icon: "🚚"
        },
        {
            key: "COMPLETED",
            label: "Delivered",
            description: "Enjoy your meal!",
            icon: "🎉"
        },
    ];

    // Determine which step is active based on status
    let activeStepIndex = -1;
    if (normalizedStatus === "PENDING" || normalizedStatus === "PLACED") {
        activeStepIndex = 0;
    } else if (normalizedStatus === "CONFIRMED") {
        activeStepIndex = 1;
    } else if (normalizedStatus === "PREPARING") {
        activeStepIndex = 2;
    } else if (normalizedStatus === "DRIVER_ASSIGNED") {
        activeStepIndex = 3;
    } else if (normalizedStatus === "PICKED_UP") {
        activeStepIndex = 4;
    } else if (normalizedStatus === "DELIVERING") {
        activeStepIndex = 5;
    } else if (normalizedStatus === "COMPLETED" || normalizedStatus === "DELIVERED") {
        activeStepIndex = 6;
    }

    timeline.innerHTML = `
        <h3 style="margin-bottom: 1.5rem;">Order Progress</h3>
        ${steps
            .map((s, idx) => {
                let stepClass = "";
                if (idx < activeStepIndex) stepClass = "completed";
                else if (idx === activeStepIndex) stepClass = "active";

                return `
                <div class="tracking-step ${stepClass}">
                    <div style="display: flex; align-items: flex-start;">
                        <span style="font-size: 1.5rem; margin-right: 0.75rem;">${s.icon}</span>
                        <div>
                            <strong style="display: block; margin-bottom: 0.25rem;">${s.label}</strong>
                            <span style="color: var(--text-light); font-size: 0.85rem;">${s.description}</span>
                        </div>
                    </div>
                </div>
            `;
            })
            .join("")}
    `;
}


function showToast(message, type = "") {
    const toast = document.getElementById("toast");
    toast.textContent = message;
    toast.className = `toast ${type}`;
    setTimeout(() => toast.classList.add("hidden"), 3000);
}

// fallback
function getMockRestaurants() {
    return [
        { restaurant_id: "r1", name: "Golden Dragon", cuisine: "Chinese", avg_rating: 4.7, delivery_time: "25-35", image_url: "https://images.unsplash.com/photo-1552566626-52f8b828add9?w=400" },
        { restaurant_id: "r2", name: "Pizza Paradise", cuisine: "Italian", avg_rating: 4.5, delivery_time: "30-40", image_url: "https://images.unsplash.com/photo-1565299624946-b28f40a0ae38?w=400" },
        { restaurant_id: "r3", name: "Sakura Sushi", cuisine: "Japanese", avg_rating: 4.8, delivery_time: "20-30", image_url: "https://images.unsplash.com/photo-1579871494447-9811cf80d66c?w=400" },
        { restaurant_id: "r4", name: "Taco Fiesta", cuisine: "Mexican", avg_rating: 4.3, delivery_time: "25-35", image_url: "https://images.unsplash.com/photo-1565299585323-38d6b0865b47?w=400" },
        { restaurant_id: "r5", name: "Curry House", cuisine: "Indian", avg_rating: 4.6, delivery_time: "30-45", image_url: "https://images.unsplash.com/photo-1585937421612-70a008356fbe?w=400" },
        { restaurant_id: "r6", name: "Burger Barn", cuisine: "American", avg_rating: 4.4, delivery_time: "20-30", image_url: "https://images.unsplash.com/photo-1568901346375-23c9450c58cd?w=400" },
    ];
}

function getMockMenu() {
    return [
        { item_id: "m1", name: "Kung Pao Chicken", description: "Spicy diced chicken with peanuts", price: 14.99 },
        { item_id: "m2", name: "Fried Rice", description: "Classic egg fried rice", price: 10.99 },
        { item_id: "m3", name: "Spring Rolls", description: "Crispy vegetable spring rolls (4pc)", price: 7.99 },
        { item_id: "m4", name: "Mapo Tofu", description: "Silken tofu in spicy sauce", price: 12.99 },
        { item_id: "m5", name: "Wonton Soup", description: "Pork wontons in clear broth", price: 8.99 },
    ];
}

// ========================================
// Driver Interface Functions
// ========================================

function showDriverInterface() {
    // Hide customer navigation items and replace with driver navigation
    const navLinks = document.querySelector('.nav-links');
    if (navLinks) {
        navLinks.innerHTML = `
            <a href="#" onclick="showPage('driver-dashboard')">Dashboard</a>
            <a href="#" onclick="showPage('driver-deliveries')">My Deliveries</a>
        `;
    }

    console.log("Driver interface activated");
}

async function loadDriverDashboard() {
    const dashboard = document.getElementById("driver-dashboard-content");
    if (!dashboard) return;

    // Check driver registration status
    let driverStatus = "Checking...";
    let driverInfo = null;
    try {
        driverInfo = await apiCall(`/drivers/${currentUser.sub}`);
        if (driverInfo) {
            driverStatus = driverInfo.status === 'available' ? '✅ Available' : `⚠️ ${driverInfo.status}`;
        }
    } catch (err) {
        driverStatus = '❌ Not Registered';
        console.error("Driver not found:", err);
    }

    dashboard.innerHTML = `
        <div style="text-align: center; padding: 2rem;">
            <h2>🚗 Driver Dashboard</h2>
            <p style="color: var(--text-light); margin-bottom: 2rem;">
                Waiting for delivery assignments...
            </p>

            <div style="background: var(--white); padding: 2rem; border-radius: var(--radius); margin: 2rem auto; max-width: 500px;">
                <h3>Status</h3>
                <p style="font-size: 1.2rem;">
                    ${driverStatus}
                </p>
                ${!driverInfo ? `
                    <div style="margin-top: 1rem; padding: 1rem; background: var(--warning-bg); border-radius: 6px;">
                        <p style="color: var(--warning); margin: 0;">
                            ⚠️ Driver profile not found. Please complete your profile to receive orders.
                        </p>
                        <button class="btn btn-primary" onclick="showPage('profile')" style="margin-top: 0.5rem;">
                            Complete Profile
                        </button>
                    </div>
                ` : ''}
            </div>

            <div style="background: var(--light-bg); padding: 1.5rem; border-radius: var(--radius); margin-top: 1rem;">
                <p><strong>📱 You will receive delivery offers here</strong></p>
                <p style="color: var(--text-light); font-size: 0.9rem; margin-top: 0.5rem;">
                    When a customer places an order, you'll see a popup modal with:<br>
                    • Pickup location<br>
                    • Delivery location<br>
                    • Distance & estimated payout<br>
                    • 2-minute countdown to accept/decline
                </p>
            </div>

            <div style="margin-top: 2rem;">
                <button class="btn btn-secondary" onclick="showPage('driver-deliveries')">
                    📋 View My Deliveries
                </button>
            </div>
        </div>
    `;
}

function testDriverOffer() {
    // Simulate a driver offer for testing
    const testOffer = {
        offer_id: "test-" + Date.now(),
        delivery_id: "delivery-test-123",
        order_id: "order-test-456",
        offer_details: {
            pickup_address: "37.7749,-122.4194 (Downtown SF)",
            delivery_address: "37.7933,-122.4417 (Pacific Heights)",
            estimated_distance_km: 3.2,
            estimated_payout: 9.80
        },
        expires_at: new Date(Date.now() + 120000).toISOString() // 2 minutes from now
    };

    handleDriverOffer(testOffer);
}

async function loadDriverDeliveries() {
    const container = document.getElementById("driver-deliveries-list");
    if (!container) return;

    container.innerHTML = '<p>Loading deliveries...</p>';

    try {
        const data = await apiCall("/deliveries");
        let deliveries = data.deliveries || [];

        if (deliveries.length === 0) {
            container.innerHTML = `
                <div class="empty-state">
                    <h3>No Deliveries Yet</h3>
                    <p>Your completed deliveries will appear here</p>
                </div>
            `;
            return;
        }

        // Sort deliveries by created_at (most recent first)
        deliveries.sort((a, b) => {
            const dateA = new Date(a.created_at || a.updated_at);
            const dateB = new Date(b.created_at || b.updated_at);
            return dateB - dateA; // Descending order (newest first)
        });

        container.innerHTML = deliveries.map(delivery => {
            // Determine status badge color
            const statusUpper = (delivery.status || '').toUpperCase();
            let badgeColor, badgeBg;

            if (statusUpper === 'DELIVERED' || statusUpper === 'COMPLETED') {
                badgeColor = '#1B5E20';
                badgeBg = '#C8E6C9';
            } else if (statusUpper === 'DELIVERING') {
                badgeColor = '#1565C0';
                badgeBg = '#BBDEFB';
            } else if (statusUpper === 'PICKED_UP') {
                badgeColor = '#E65100';
                badgeBg = '#FFE0B2';
            } else if (statusUpper === 'ASSIGNED') {
                badgeColor = '#6A1B9A';
                badgeBg = '#E1BEE7';
            } else {
                badgeColor = '#F57F17';
                badgeBg = '#FFF9C4';
            }

            return `
                <div class="order-card delivery-card-clickable"
                     style="margin-bottom: 1rem; cursor: pointer; transition: transform 0.2s, box-shadow 0.2s;"
                     onclick="navigateToDeliveryDetails('${delivery.delivery_id}', '${delivery.order_id}')"
                     onmouseenter="this.style.transform='translateY(-2px)'; this.style.boxShadow='0 4px 12px rgba(0,0,0,0.15)'"
                     onmouseleave="this.style.transform='translateY(0)'; this.style.boxShadow='0 2px 8px rgba(0,0,0,0.1)'">
                    <div style="display: flex; justify-content: space-between; align-items: center;">
                        <div>
                            <h4>Delivery #${delivery.delivery_id.substring(0, 8)}</h4>
                            <p style="color: var(--text-light); font-size: 0.9rem;">
                                Order: ${delivery.order_id?.substring(0, 8)}
                            </p>
                        </div>
                        <span style="padding: 0.4rem 0.8rem; border-radius: 12px; font-size: 0.85rem; font-weight: 600; color: ${badgeColor}; background-color: ${badgeBg};">
                            ${delivery.status}
                        </span>
                    </div>
                    <p style="margin-top: 0.5rem;">
                        📍 ${delivery.customer_address || 'N/A'}
                    </p>
                    <div style="display: flex; justify-content: space-between; align-items: center; margin-top: 0.5rem;">
                        <p style="color: var(--text-light); font-size: 0.9rem; margin: 0;">
                            🕐 ${new Date(delivery.created_at).toLocaleString()}
                        </p>
                        <p style="color: var(--primary); font-size: 0.85rem; margin: 0; font-weight: 500;">
                            Click to view details →
                        </p>
                    </div>
                </div>
            `;
        }).join('');

    } catch (err) {
        console.error("Error loading deliveries:", err);
        container.innerHTML = '<p>Failed to load deliveries</p>';
    }
}

// Navigate to delivery details page
function navigateToDeliveryDetails(deliveryId, orderId) {
    console.log(`Navigating to delivery details: ${deliveryId}`);

    // Store the delivery ID and order ID in localStorage for the details page
    localStorage.setItem('currentDeliveryId', deliveryId);
    localStorage.setItem('currentOrderId', orderId);

    // Navigate to delivery details page
    showPage('driver-delivery-details');

    // Load the delivery details
    loadDeliveryDetails(deliveryId, orderId);
}

// Load delivery details (reuse existing function or fetch fresh data)
async function loadDeliveryDetails(deliveryId, orderId) {
    const container = document.getElementById('delivery-detail-content');
    if (!container) {
        console.error('delivery-detail-content container not found');
        return;
    }

    container.innerHTML = '<p>Loading delivery details...</p>';

    try {
        console.log(`Fetching details for delivery: ${deliveryId}, order: ${orderId}`);

        // Fetch both delivery and order data
        const [deliveryData, orderData] = await Promise.all([
            apiCall(`/deliveries/${deliveryId}`),
            apiCall(`/orders/${orderId}`)
        ]);

        console.log('Delivery data:', deliveryData);
        console.log('Order data:', orderData);

        const delivery = deliveryData.delivery || deliveryData;
        const order = orderData.order || orderData;

        if (!delivery && !order) {
            container.innerHTML = '<p>Delivery not found</p>';
            return;
        }

        // Display the delivery details
        renderDeliveryDetailsContent(order, delivery, deliveryId);

    } catch (err) {
        console.error("Error loading delivery details:", err);
        container.innerHTML = `
            <div style="padding: 2rem; text-align: center;">
                <p style="color: var(--danger); margin-bottom: 1rem;">Failed to load delivery details</p>
                <p style="color: var(--text-light); font-size: 0.9rem;">${err.message || 'Unknown error'}</p>
                <button class="btn btn-secondary" onclick="showPage('driver-deliveries')" style="margin-top: 1rem;">
                    ← Back to My Deliveries
                </button>
            </div>
        `;
    }
}

function renderDeliveryDetailsContent(order, delivery, deliveryId) {
    const container = document.getElementById('delivery-detail-content');
    if (!container) return;

    // Use data from both order and delivery - FIX: Use correct field names
    const items = order?.items || [];
    const totalItems = items.reduce((sum, item) => sum + (parseInt(item.quantity) || 0), 0);
    const orderStatus = delivery?.status || order?.status || 'UNKNOWN';
    const deliveryAddress = delivery?.customer_address || order?.delivery_address_display || order?.delivery_address || 'N/A';
    const restaurantAddress = order?.restaurant_address_display || order?.restaurant_address || 'Restaurant address';
    const orderTotal = parseFloat(order?.total) || 0;

    // Determine status badge color
    const statusUpper = orderStatus.toUpperCase();
    let badgeColor, badgeBg;

    if (statusUpper === 'DELIVERED' || statusUpper === 'COMPLETED') {
        badgeColor = '#1B5E20';
        badgeBg = '#C8E6C9';
    } else if (statusUpper === 'DELIVERING') {
        badgeColor = '#1565C0';
        badgeBg = '#BBDEFB';
    } else if (statusUpper === 'PICKED_UP') {
        badgeColor = '#E65100';
        badgeBg = '#FFE0B2';
    } else if (statusUpper === 'ASSIGNED' || statusUpper === 'DRIVER_ASSIGNED') {
        badgeColor = '#6A1B9A';
        badgeBg = '#E1BEE7';
    } else {
        badgeColor = '#F57F17';
        badgeBg = '#FFF9C4';
    }

    // Build timeline data
    const timelineEvents = [];

    if (delivery?.created_at) {
        timelineEvents.push({
            icon: '📋',
            label: 'Order Assigned',
            time: new Date(delivery.created_at).toLocaleString(),
            color: '#6A1B9A'
        });
    }

    if (delivery?.pickup_time) {
        timelineEvents.push({
            icon: '✅',
            label: 'Picked Up',
            time: new Date(delivery.pickup_time).toLocaleString(),
            color: '#E65100'
        });
    }

    if (delivery?.delivery_time) {
        timelineEvents.push({
            icon: '🎉',
            label: 'Delivered',
            time: new Date(delivery.delivery_time).toLocaleString(),
            color: '#1B5E20'
        });
    }

    container.innerHTML = `
        <!-- Back Button at Top -->
        <div style="margin-bottom: 1.5rem;">
            <button class="btn btn-outline" onclick="showPage('driver-deliveries')" style="display: inline-flex; align-items: center; gap: 0.5rem;">
                <span>←</span>
                <span>Back to My Deliveries</span>
            </button>
        </div>

        <!-- Header Card -->
        <div style="background: linear-gradient(135deg, #e63946 0%, #c1121f 100%); border-radius: var(--radius); padding: 2rem; margin-bottom: 1.5rem; color: white; box-shadow: var(--shadow);">
            <div style="display: flex; justify-content: space-between; align-items: start; flex-wrap: wrap; gap: 1rem;">
                <div>
                    <p style="opacity: 0.9; font-size: 0.9rem; margin: 0 0 0.5rem 0;">Delivery ID</p>
                    <h2 style="margin: 0; font-size: 1.75rem; font-weight: 700;">#${deliveryId.substring(0, 8).toUpperCase()}</h2>
                </div>
                <span style="padding: 0.6rem 1.2rem; border-radius: 20px; font-size: 0.9rem; font-weight: 600; color: ${badgeColor}; background-color: white; box-shadow: 0 4px 12px rgba(0,0,0,0.1);">
                    ${orderStatus}
                </span>
            </div>
        </div>

        <!-- Order Summary Card -->
        <div style="background: var(--white); border-radius: var(--radius); padding: 1.75rem; margin-bottom: 1.5rem; box-shadow: var(--shadow); border: 1px solid var(--border);">
            <h3 style="margin: 0 0 1.25rem 0; font-size: 1.15rem; color: var(--text); display: flex; align-items: center; gap: 0.5rem;">
                <span style="font-size: 1.3rem;">📦</span>
                Order Summary
            </h3>
            <div style="display: grid; grid-template-columns: repeat(auto-fit, minmax(140px, 1fr)); gap: 1.25rem;">
                <div style="padding: 1rem; background: var(--bg); border-radius: var(--radius); border-left: 4px solid var(--primary);">
                    <p style="color: var(--text-light); font-size: 0.85rem; margin: 0 0 0.4rem 0; font-weight: 500;">Order ID</p>
                    <p style="font-weight: 600; font-size: 1rem; margin: 0; color: var(--text);">#${order?.order_id?.substring(0, 8).toUpperCase() || 'N/A'}</p>
                </div>
                <div style="padding: 1rem; background: var(--bg); border-radius: var(--radius); border-left: 4px solid var(--secondary);">
                    <p style="color: var(--text-light); font-size: 0.85rem; margin: 0 0 0.4rem 0; font-weight: 500;">Total Items</p>
                    <p style="font-weight: 600; font-size: 1rem; margin: 0; color: var(--text);">${totalItems}</p>
                </div>
                <div style="padding: 1rem; background: var(--bg); border-radius: var(--radius); border-left: 4px solid var(--success);">
                    <p style="color: var(--text-light); font-size: 0.85rem; margin: 0 0 0.4rem 0; font-weight: 500;">Total Amount</p>
                    <p style="font-weight: 700; font-size: 1.15rem; margin: 0; color: var(--success);">$${orderTotal.toFixed(2)}</p>
                </div>
            </div>
        </div>

        <!-- Order Items Card -->
        ${items.length > 0 ? `
            <div style="background: var(--white); border-radius: var(--radius); padding: 1.75rem; margin-bottom: 1.5rem; box-shadow: var(--shadow); border: 1px solid var(--border);">
                <h3 style="margin: 0 0 1.25rem 0; font-size: 1.15rem; color: var(--text); display: flex; align-items: center; gap: 0.5rem;">
                    <span style="font-size: 1.3rem;">🍽️</span>
                    Order Items
                </h3>
                <div style="display: flex; flex-direction: column; gap: 0.75rem;">
                    ${items.map((item, idx) => {
                        const unitPrice = parseInt(item.unit_price_cents) / 100;
                        const quantity = parseInt(item.quantity);
                        const lineTotal = unitPrice * quantity;
                        return `
                            <div style="display: flex; justify-content: space-between; align-items: center; padding: 1rem; background: var(--bg); border-radius: var(--radius); ${idx === items.length - 1 ? '' : 'border-bottom: 2px solid var(--border);'}">
                                <div style="flex: 1;">
                                    <p style="font-weight: 600; margin: 0 0 0.25rem 0; color: var(--text); font-size: 1rem;">${item.name}</p>
                                    <p style="color: var(--text-light); font-size: 0.875rem; margin: 0;">
                                        $${unitPrice.toFixed(2)} × ${quantity}
                                    </p>
                                </div>
                                <p style="font-weight: 700; font-size: 1.1rem; margin: 0; color: var(--success);">$${lineTotal.toFixed(2)}</p>
                            </div>
                        `;
                    }).join('')}
                </div>
            </div>
        ` : ''}

        <!-- Locations Card -->
        <div style="background: var(--white); border-radius: var(--radius); padding: 1.75rem; margin-bottom: 1.5rem; box-shadow: var(--shadow); border: 1px solid var(--border);">
            <h3 style="margin: 0 0 1.25rem 0; font-size: 1.15rem; color: var(--text); display: flex; align-items: center; gap: 0.5rem;">
                <span style="font-size: 1.3rem;">📍</span>
                Locations
            </h3>
            <div style="display: flex; flex-direction: column; gap: 1.25rem;">
                <div style="padding: 1.25rem; background: #ffe5e8; border-radius: var(--radius); border-left: 4px solid var(--primary);">
                    <p style="font-weight: 600; margin: 0 0 0.5rem 0; color: var(--primary-dark); font-size: 0.9rem;">🍽️ PICKUP LOCATION</p>
                    <p style="margin: 0; color: var(--text); font-size: 1rem; line-height: 1.5;">${restaurantAddress}</p>
                </div>
                <div style="padding: 1.25rem; background: #ddf0f7; border-radius: var(--radius); border-left: 4px solid var(--secondary);">
                    <p style="font-weight: 600; margin: 0 0 0.5rem 0; color: #2c5f7c; font-size: 0.9rem;">🏠 DELIVERY LOCATION</p>
                    <p style="margin: 0; color: var(--text); font-size: 1rem; line-height: 1.5;">${deliveryAddress}</p>
                </div>
            </div>
        </div>

        <!-- Timeline Card -->
        ${timelineEvents.length > 0 ? `
            <div style="background: var(--white); border-radius: var(--radius); padding: 1.75rem; margin-bottom: 1.5rem; box-shadow: var(--shadow); border: 1px solid var(--border);">
                <h3 style="margin: 0 0 1.25rem 0; font-size: 1.15rem; color: var(--text); display: flex; align-items: center; gap: 0.5rem;">
                    <span style="font-size: 1.3rem;">⏱️</span>
                    Delivery Timeline
                </h3>
                <div style="position: relative; padding-left: 2.5rem;">
                    ${timelineEvents.map((event, idx) => `
                        <div style="position: relative; padding-bottom: ${idx === timelineEvents.length - 1 ? '0' : '1.75rem'};">
                            ${idx !== timelineEvents.length - 1 ? `
                                <div style="position: absolute; left: -1.65rem; top: 2.5rem; bottom: -0.5rem; width: 2px; background: linear-gradient(to bottom, ${event.color} 0%, var(--border) 100%);"></div>
                            ` : ''}
                            <div style="position: absolute; left: -2.25rem; top: 0.25rem; width: 2.5rem; height: 2.5rem; background: var(--white); border: 3px solid ${event.color}; border-radius: 50%; display: flex; align-items: center; justify-content: center; font-size: 1.1rem; box-shadow: var(--shadow);">
                                ${event.icon}
                            </div>
                            <div style="padding: 0.75rem 1.25rem; background: var(--bg); border-radius: var(--radius); border-left: 3px solid ${event.color};">
                                <p style="font-weight: 600; margin: 0 0 0.3rem 0; color: var(--text); font-size: 1rem;">${event.label}</p>
                                <p style="color: var(--text-light); margin: 0; font-size: 0.9rem;">📅 ${event.time}</p>
                            </div>
                        </div>
                    `).join('')}
                </div>
            </div>
        ` : ''}
    `;
}

// ========================================
// Driver Offer Functions
// ========================================

// Store current offer data for when driver accepts
let currentOffer = null;

function handleDriverOffer(data) {
    console.log("Driver offer received:", data);

    const {
        offer_id,
        delivery_id,
        order_id,
        offer_details,
        expires_at
    } = data;

    // Store offer data for later use
    currentOffer = {
        offerId: offer_id,
        deliveryId: delivery_id,
        orderId: order_id,
        pickupAddress: offer_details.pickup_address,
        deliveryAddress: offer_details.delivery_address,
        restaurantLat: offer_details.restaurant_lat,
        restaurantLng: offer_details.restaurant_lng,
        deliveryLat: offer_details.delivery_lat,
        deliveryLng: offer_details.delivery_lng,
        distance: parseFloat(offer_details.estimated_distance_km),
        payout: parseFloat(offer_details.estimated_payout),
        expiresAt: expires_at,
    };

    // Show notification modal
    showDriverOfferModal(currentOffer);

    // Start countdown timer
    startOfferCountdown(offer_id, expires_at);

    // Play notification sound (optional)
    playNotificationSound();
}

function showDriverOfferModal(offer) {
    // Remove any existing offer modals
    const existingModals = document.querySelectorAll('.driver-offer-modal');
    existingModals.forEach(modal => modal.remove());

    // Create modal UI
    const modal = document.createElement("div");
    modal.className = "driver-offer-modal";
    modal.id = `offer-${offer.offerId}`;

    const pickupAddr = formatAddress(offer.pickupAddress);
    const deliveryAddr = formatAddress(offer.deliveryAddress);

    modal.innerHTML = `
        <div class="modal-overlay"></div>
        <div class="modal-content">
            <h2>🚗 New Delivery Offer</h2>
            <div class="offer-details">
                <p><strong>Pickup:</strong> ${pickupAddr}</p>
                <p><strong>Delivery:</strong> ${deliveryAddr}</p>
                <p><strong>Distance:</strong> ${offer.distance.toFixed(1)} km</p>
                <p><strong>Payout:</strong> $${offer.payout.toFixed(2)}</p>
                <p class="countdown" id="countdown-${offer.offerId}">Calculating...</p>
            </div>
            <div class="offer-actions">
                <button class="btn-accept" onclick="respondToOffer('${offer.offerId}', 'accept')">
                    ✓ Accept
                </button>
                <button class="btn-reject" onclick="respondToOffer('${offer.offerId}', 'reject')">
                    ✗ Decline
                </button>
            </div>
        </div>
    `;

    document.body.appendChild(modal);
}

async function respondToOffer(offerId, action) {
    try {
        const apiBase = window.APP_CONFIG?.API_BASE_URL || "";

        if (!authToken) {
            showToast("Please log in to respond to offers", "error");
            return;
        }

        const response = await fetch(
            `${apiBase}/deliveries/offers/${offerId}/respond`,
            {
                method: "POST",
                headers: {
                    "Content-Type": "application/json",
                    "Authorization": `Bearer ${authToken}`,
                },
                body: JSON.stringify({ action }),
            }
        );

        const result = await response.json();

        if (response.ok) {
            // Remove modal
            const modal = document.getElementById(`offer-${offerId}`);
            if (modal) modal.remove();

            // Show success message
            if (action === "accept") {
                showToast("Delivery accepted! Loading active delivery...", "success");
                // Navigate to active delivery page
                if (currentOffer && currentOffer.deliveryId) {
                    setTimeout(() => {
                        showPage("driver-active-delivery");
                        showActiveDelivery({
                            delivery_id: currentOffer.deliveryId,
                            order_id: currentOffer.orderId,
                        });
                    }, 500);
                } else {
                    // Fallback to dashboard if offer data not available
                    setTimeout(() => loadDriverDashboard(), 500);
                }
            } else {
                showToast("Offer declined", "info");
            }
        } else {
            showToast(result.error || "Failed to respond to offer", "error");
        }
    } catch (error) {
        console.error("Error responding to offer:", error);
        showToast("Network error", "error");
    }
}

function startOfferCountdown(offerId, expiresAt) {
    const countdownEl = document.getElementById(`countdown-${offerId}`);
    if (!countdownEl) return;

    const interval = setInterval(() => {
        const now = new Date();
        const expires = new Date(expiresAt);
        const remaining = Math.max(0, Math.floor((expires - now) / 1000));

        if (remaining <= 0) {
            clearInterval(interval);
            countdownEl.textContent = "EXPIRED";
            countdownEl.classList.add("expired");
            // Auto-close modal after 2 seconds
            setTimeout(() => {
                const modal = document.getElementById(`offer-${offerId}`);
                if (modal) modal.remove();
            }, 2000);
        } else {
            const minutes = Math.floor(remaining / 60);
            const seconds = remaining % 60;
            countdownEl.textContent = `Expires in: ${minutes}:${seconds.toString().padStart(2, '0')}`;
        }
    }, 1000);
}

function playNotificationSound() {
    // Optional: Add audio notification
    // Audio file not included, using browser notification instead
    try {
        // Use browser notification API (if permission granted)
        if ("Notification" in window && Notification.permission === "granted") {
            new Notification("New Delivery Offer", {
                body: "You have a new delivery offer!",
            });
        }
    } catch (e) {
        console.log("Notification not available:", e);
    }
}

function formatAddress(address) {
    if (typeof address === 'string') {
        // If it's a Python dict string representation, try to parse it
        if (address.startsWith('{') || address.startsWith("{'")) {
            try {
                // Try to convert Python dict string to JSON
                const jsonStr = address.replace(/'/g, '"');
                const parsed = JSON.parse(jsonStr);
                if (parsed.address) {
                    return formatAddress(parsed.address);
                }
            } catch (e) {
                // If parsing fails, check if it contains 'address':
                const match = address.match(/['"]address['"]\s*:\s*['"]([^'"]+)['"]/);
                if (match && match[1]) {
                    return match[1];
                }
            }
        }
        // If it's a lat,lng string, return a placeholder
        if (address.match(/^-?\d+\.?\d*,-?\d+\.?\d*$/)) {
            return `Location (${address})`;
        }
        return address;
    }
    if (typeof address === 'object' && address !== null) {
        if (address.address) {
            return formatAddress(address.address);
        }
    }
    return String(address);
}

async function showActiveDelivery(acceptanceData) {
    showPage("driver-active-delivery");

    const container = document.getElementById("active-delivery-content");
    if (!container) return;

    // Store current delivery info globally
    window.currentDeliveryData = acceptanceData;

    // Show loading state
    container.innerHTML = `
        <div style="padding: 1rem;">
            <h2>🚗 Active Delivery</h2>
            <p style="color: var(--text-light);">Loading delivery details...</p>
        </div>
    `;

    try {
        const apiBase = window.APP_CONFIG?.API_BASE_URL || "";

        // Helper function to fetch order with retry (handles timing issues with step functions)
        const fetchOrderWithRetry = async (orderId, maxRetries = 3, delayMs = 1000) => {
            for (let attempt = 1; attempt <= maxRetries; attempt++) {
                try {
                    console.log(`Fetching order (attempt ${attempt}/${maxRetries}):`, orderId);
                    const orderResponse = await fetch(
                        `${apiBase}/orders/${orderId}`,
                        {
                            headers: {
                                "Authorization": `Bearer ${authToken}`,
                            },
                        }
                    );

                    console.log("Order response status:", orderResponse.status);

                    if (orderResponse.ok) {
                        const data = await orderResponse.json();
                        console.log("Order data received:", data);
                        return data.order || data;
                    }

                    // If forbidden/not found and not last attempt, wait and retry
                    if ((orderResponse.status === 403 || orderResponse.status === 404) && attempt < maxRetries) {
                        console.log(`Order not accessible yet, waiting ${delayMs}ms before retry...`);
                        await new Promise(resolve => setTimeout(resolve, delayMs));
                        continue;
                    }

                    // Other errors or last attempt failed
                    const errorData = await orderResponse.json().catch(() => ({}));
                    console.error("Order fetch failed:", orderResponse.status, errorData);
                    throw new Error(`Failed to fetch order: ${errorData.message || errorData.error || orderResponse.status}`);
                } catch (error) {
                    if (attempt === maxRetries) {
                        throw error;
                    }
                    console.log(`Error fetching order, retrying... (${error.message})`);
                    await new Promise(resolve => setTimeout(resolve, delayMs));
                }
            }
        };

        // Fetch the full order data with retry logic (Step Functions may still be updating)
        const orderData = await fetchOrderWithRetry(acceptanceData.order_id);

        // Store full order data
        currentOrderData = orderData;

        // Render the UI with actual data
        // Prefer display addresses (street names) over raw coordinates
        const restaurantAddr = formatAddress(orderData.restaurant_address_display || orderData.restaurant_location);
        const deliveryAddr = formatAddress(orderData.delivery_address_display || orderData.delivery_address);

        container.innerHTML = `
            <div style="padding: 1rem;">
                <h2>🚗 Active Delivery</h2>
                <p style="color: var(--text-light);">Navigate to pickup location</p>

                <div style="margin: 1rem 0;">
                    <div id="driver-delivery-map" style="height: 400px; border-radius: 8px;"></div>
                </div>

                <div style="background: var(--white); padding: 1rem; border-radius: 8px; margin-top: 1rem;">
                    <h3 style="margin-top: 0;">Route</h3>
                    <div style="display: flex; flex-direction: column; gap: 0.5rem;">
                        <div style="display: flex; align-items: center;">
                            <span style="margin-right: 0.5rem;">📍</span>
                            <span>Your Location</span>
                        </div>
                        <div style="margin-left: 1rem; border-left: 2px dashed var(--text-light); height: 20px;"></div>
                        <div style="display: flex; align-items: center;">
                            <span style="margin-right: 0.5rem;">🏪</span>
                            <span>Pickup: ${restaurantAddr}</span>
                        </div>
                        <div style="margin-left: 1rem; border-left: 2px dashed var(--text-light); height: 20px;"></div>
                        <div style="display: flex; align-items: center;">
                            <span style="margin-right: 0.5rem;">🏠</span>
                            <span>Delivery: ${deliveryAddr}</span>
                        </div>
                    </div>

                    <div style="margin-top: 1rem; padding: 1rem; background: var(--light-bg); border-radius: 6px;">
                        <p><strong>Estimated Time:</strong> <span id="delivery-eta">Calculating...</span></p>
                    </div>
                </div>

                <div style="margin-top: 1rem;">
                    <button class="btn btn-primary btn-full" onclick="completePickup()">
                        ✓ Picked Up from Restaurant
                    </button>
                </div>
            </div>
        `;

        // Initialize map with actual order data (reuse customer map logic)
        setTimeout(() => initDriverDeliveryMap(orderData), 100);

    } catch (error) {
        console.error("Error loading delivery details:", error);
        console.error("Error stack:", error.stack);
        console.error("Acceptance data:", acceptanceData);
        container.innerHTML = `
            <div style="padding: 1rem;">
                <h2>🚗 Active Delivery</h2>
                <p style="color: var(--error);">Failed to load delivery details</p>
                <p style="color: var(--text-light); font-size: 0.9rem; margin-top: 0.5rem;">Error: ${error.message}</p>
                <button class="btn btn-secondary" onclick="loadDriverDashboard()">Back to Dashboard</button>
            </div>
        `;
    }
}

// Store driver-specific map and markers
let driverDeliveryMap = null;
let driverRestaurantMarker = null;
let driverDestinationMarker = null;
let driverDriverMarker = null;
let driverRouteLine = null;

// Driver map animation state
let driverCurrentRoute = null;
let driverRouteAnimationInterval = null;
let driverAnimationProgress = 0;

function initDriverDeliveryMap(orderData) {
    const mapDiv = document.getElementById("driver-delivery-map");
    if (!mapDiv) return;

    // Clean up old map and markers if they exist
    if (driverDeliveryMap) {
        driverDeliveryMap.remove();
        driverDeliveryMap = null;
    }
    driverRestaurantMarker = null;
    driverDestinationMarker = null;
    driverDriverMarker = null;
    driverRouteLine = null;

    // Parse locations from order data (same as customer map)
    const restaurantLoc = parseLocation(orderData.restaurant_location, { lat: 37.7849, lng: -122.4094 });
    const deliveryLoc = parseLocation(orderData.delivery_address, { lat: 37.7749, lng: -122.4194 });

    // HYBRID APPROACH: Determine driver position based on order status
    const orderStatus = (orderData.status || "").toLowerCase();
    console.log(`🚗 [DRIVER MAP] Order status: ${orderStatus.toUpperCase()}`);

    let driverLoc = null;

    if (orderStatus === "picked_up") {
        // LOGICAL: Driver just picked up at restaurant
        console.log("📦 [DRIVER MAP] Driver at restaurant (just picked up)");
        driverLoc = restaurantLoc;
    } else if (orderStatus === "delivered") {
        // LOGICAL: Driver at customer location (just delivered)
        console.log("🏠 [DRIVER MAP] Driver at customer location (just delivered)");
        driverLoc = deliveryLoc;
    } else {
        // REAL GPS: Use driver's actual location for moving states
        console.log("🛰️ [DRIVER MAP] Using driver GPS location");
        driverLoc = orderData.driver_location ? parseLocation(orderData.driver_location) : null;

        // Fallback if no GPS
        if (!driverLoc) {
            if (orderStatus === "driver_assigned") {
                console.log("⚠️ [DRIVER MAP] No GPS - defaulting to restaurant");
                driverLoc = restaurantLoc;
            } else if (orderStatus === "delivering") {
                console.log("⚠️ [DRIVER MAP] No GPS - using midpoint");
                driverLoc = {
                    lat: (restaurantLoc.lat + deliveryLoc.lat) / 2,
                    lng: (restaurantLoc.lng + deliveryLoc.lng) / 2
                };
            } else {
                // Default fallback
                driverLoc = restaurantLoc;
            }
        }
    }

    // Initialize fresh map
    driverDeliveryMap = L.map(mapDiv).setView([restaurantLoc.lat, restaurantLoc.lng], 13);

    // Add OpenStreetMap tiles
    L.tileLayer("https://{s}.tile.openstreetmap.org/{z}/{x}/{y}.png", {
        attribution: '&copy; <a href="https://www.openstreetmap.org/copyright">OpenStreetMap</a>',
        maxZoom: 19,
    }).addTo(driverDeliveryMap);

    // Add restaurant marker
    if (restaurantLoc) {
        driverRestaurantMarker = L.marker([restaurantLoc.lat, restaurantLoc.lng], {
            icon: L.divIcon({
                className: "restaurant-marker",
                html: '<div style="background: #457b9d; color: white; padding: 10px; border-radius: 50%; width: 45px; height: 45px; display: flex; align-items: center; justify-content: center; font-size: 22px; box-shadow: 0 4px 12px rgba(0,0,0,0.3); border: 3px solid white;">🍽️</div>',
                iconSize: [45, 45],
                iconAnchor: [22.5, 22.5],
            }),
        }).addTo(driverDeliveryMap);
        driverRestaurantMarker.bindPopup("<b>Restaurant</b><br>Pickup location");
    }

    // Add destination marker
    if (deliveryLoc) {
        driverDestinationMarker = L.marker([deliveryLoc.lat, deliveryLoc.lng], {
            icon: L.divIcon({
                className: "destination-marker",
                html: '<div style="background: #2d6a4f; color: white; padding: 10px; border-radius: 50%; width: 45px; height: 45px; display: flex; align-items: center; justify-content: center; font-size: 22px; box-shadow: 0 4px 12px rgba(0,0,0,0.3); border: 3px solid white;">🏠</div>',
                iconSize: [45, 45],
                iconAnchor: [22.5, 22.5],
            }),
        }).addTo(driverDeliveryMap);
        driverDestinationMarker.bindPopup("<b>Customer</b><br>Delivery location");
    }

    // Add driver marker if available (or use default location)
    const driverPosition = driverLoc || { lat: 37.7749, lng: -122.4194 }; // Default if not available
    driverDriverMarker = L.marker([driverPosition.lat, driverPosition.lng], {
        icon: L.divIcon({
            className: "driver-marker",
            html: '<div style="background: #e63946; color: white; padding: 10px; border-radius: 50%; width: 45px; height: 45px; display: flex; align-items: center; justify-content: center; font-size: 22px; box-shadow: 0 4px 12px rgba(0,0,0,0.3); border: 3px solid white;">🚗</div>',
            iconSize: [45, 45],
            iconAnchor: [22.5, 22.5],
        }),
    }).addTo(driverDeliveryMap);
    driverDriverMarker.bindPopup("<b>You</b><br>Your location");

    // Draw actual route using OSRM
    drawDriverDeliveryRoute(driverPosition, restaurantLoc, deliveryLoc, orderStatus).then(() => {
        // Fit map to show all markers after route is drawn
        const bounds = L.latLngBounds([
            [driverPosition.lat, driverPosition.lng],
            [restaurantLoc.lat, restaurantLoc.lng],
            [deliveryLoc.lat, deliveryLoc.lng]
        ]);
        driverDeliveryMap.fitBounds(bounds, { padding: [50, 50] });
    });

    // Calculate and display ETA
    const totalDistance = calculateDistance([driverPosition.lat, driverPosition.lng], [restaurantLoc.lat, restaurantLoc.lng]) +
                         calculateDistance([restaurantLoc.lat, restaurantLoc.lng], [deliveryLoc.lat, deliveryLoc.lng]);
    const etaMinutes = Math.ceil(totalDistance * 2); // Assuming 30 km/h average speed
    const etaElement = document.getElementById("delivery-eta");
    if (etaElement) {
        etaElement.textContent = `${etaMinutes} minutes`;
    }
}

/**
 * Stop driver map animation
 */
function stopDriverMapAnimation() {
    if (driverRouteAnimationInterval) {
        clearInterval(driverRouteAnimationInterval);
        driverRouteAnimationInterval = null;
        console.log("⏹️ [DRIVER ANIMATION] Stopped");
    }
    driverAnimationProgress = 0;
}

/**
 * Animate driver marker on driver's map along a route
 */
function animateDriverMapMarker(route, duration, orderStatus) {
    if (!route || route.length < 2 || !driverDriverMarker) {
        console.warn("Cannot animate driver map: route too short or marker missing");
        return;
    }

    // Stop any existing animation
    stopDriverMapAnimation();

    console.log(`🎬 [DRIVER ANIMATION] Starting animation along ${route.length} points over ${duration/1000}s (status: ${orderStatus})`);

    driverAnimationProgress = 0;
    const startTime = Date.now();

    driverRouteAnimationInterval = setInterval(() => {
        const elapsed = Date.now() - startTime;
        const progress = Math.min(elapsed / duration, 1.0); // 0 to 1

        // Find position along route based on progress
        const totalPoints = route.length - 1;
        const currentIndex = Math.floor(progress * totalPoints);
        const nextIndex = Math.min(currentIndex + 1, totalPoints);

        // Interpolate between current and next point
        const segmentProgress = (progress * totalPoints) - currentIndex;
        const currentPoint = route[currentIndex];
        const nextPoint = route[nextIndex];

        const lat = currentPoint[0] + (nextPoint[0] - currentPoint[0]) * segmentProgress;
        const lng = currentPoint[1] + (nextPoint[1] - currentPoint[1]) * segmentProgress;

        // Update driver marker position
        if (driverDriverMarker) {
            driverDriverMarker.setLatLng([lat, lng]);
        }

        driverAnimationProgress = progress;

        // Stop when complete
        if (progress >= 1.0) {
            console.log("✅ [DRIVER ANIMATION] Completed");
            stopDriverMapAnimation();
        }
    }, 100); // Update every 100ms for smooth animation
}

/**
 * Draw route on driver's delivery map using OSRM with animation
 */
async function drawDriverDeliveryRoute(driverLoc, restaurantLoc, deliveryLoc, orderStatus) {
    if (!driverDeliveryMap) return;

    // Remove existing route
    if (driverRouteLine) {
        driverDeliveryMap.removeLayer(driverRouteLine);
    }

    const status = (orderStatus || "").toLowerCase();
    let routeCoordinates = [];

    // Determine which route to show based on order status
    if (status === "driver_assigned") {
        // Driver going to restaurant - animate
        console.log("🚗 [DRIVER MAP ROUTE] Fetching route to restaurant");

        const distance = calculateDistance([driverLoc.lat, driverLoc.lng], [restaurantLoc.lat, restaurantLoc.lng]);
        if (distance > 0.01) { // More than 10 meters away
            routeCoordinates = await fetchRoute(driverLoc, restaurantLoc);

            driverRouteLine = L.polyline(routeCoordinates, {
                color: '#e63946',
                weight: 4,
                opacity: 0.7,
            }).addTo(driverDeliveryMap);

            // Animate driver marker along route
            driverCurrentRoute = routeCoordinates;
            animateDriverMapMarker(routeCoordinates, animationDuration, status);
        } else {
            console.log("Driver already at restaurant");
            routeCoordinates = await fetchRoute(restaurantLoc, deliveryLoc);
            driverRouteLine = L.polyline(routeCoordinates, {
                color: '#4caf50',
                weight: 4,
                opacity: 0.5,
                dashArray: '10, 10',
            }).addTo(driverDeliveryMap);
            stopDriverMapAnimation();
        }

    } else if (status === "picked_up") {
        // Show route from restaurant to customer (driver is at restaurant, stationary)
        console.log("📦 [DRIVER MAP ROUTE] Showing route to customer (no animation)");
        routeCoordinates = await fetchRoute(restaurantLoc, deliveryLoc);

        driverRouteLine = L.polyline(routeCoordinates, {
            color: '#4caf50',
            weight: 4,
            opacity: 0.7,
        }).addTo(driverDeliveryMap);

        stopDriverMapAnimation(); // No animation - driver stationary

    } else if (status === "delivering") {
        // Driver going from restaurant to customer - animate
        console.log("🚗 [DRIVER MAP ROUTE] Fetching route to customer (from restaurant)");

        // Start from restaurant (where driver just picked up)
        const startLoc = restaurantLoc;
        routeCoordinates = await fetchRoute(startLoc, deliveryLoc);

        driverRouteLine = L.polyline(routeCoordinates, {
            color: '#4caf50',
            weight: 4,
            opacity: 0.7,
        }).addTo(driverDeliveryMap);

        // Animate driver marker along route
        driverCurrentRoute = routeCoordinates;
        animateDriverMapMarker(routeCoordinates, animationDuration, status);

    } else {
        // Default: show full journey
        const toRestaurant = await fetchRoute(driverLoc, restaurantLoc);
        const toCustomer = await fetchRoute(restaurantLoc, deliveryLoc);
        routeCoordinates = [...toRestaurant, ...toCustomer];

        driverRouteLine = L.polyline(routeCoordinates, {
            color: '#e63946',
            weight: 4,
            opacity: 0.7,
        }).addTo(driverDeliveryMap);
    }
}

function calculateDistance(loc1, loc2) {
    // Haversine formula for distance in km
    const R = 6371;
    const dLat = (loc2[0] - loc1[0]) * Math.PI / 180;
    const dLon = (loc2[1] - loc1[1]) * Math.PI / 180;
    const a = Math.sin(dLat/2) * Math.sin(dLat/2) +
              Math.cos(loc1[0] * Math.PI / 180) * Math.cos(loc2[0] * Math.PI / 180) *
              Math.sin(dLon/2) * Math.sin(dLon/2);
    const c = 2 * Math.atan2(Math.sqrt(a), Math.sqrt(1-a));
    return R * c;
}

async function completePickup() {
    if (!currentOffer || !currentOffer.deliveryId) {
        showToast("Error: Delivery information not available", "error");
        return;
    }

    try {
        const apiBase = window.APP_CONFIG?.API_BASE_URL || "";
        const response = await fetch(
            `${apiBase}/deliveries/${currentOffer.deliveryId}/pickup`,
            {
                method: "PATCH",
                headers: {
                    "Authorization": `Bearer ${authToken}`,
                },
            }
        );

        if (response.ok) {
            showToast("Marked as picked up! Navigate to customer.", "success");

            // Update driver map to show route from restaurant to customer
            if (currentOrderData && driverDeliveryMap) {
                const restaurantLoc = parseLocation(currentOrderData.restaurant_location, { lat: 37.7849, lng: -122.4094 });
                const deliveryLoc = parseLocation(currentOrderData.delivery_address, { lat: 37.7749, lng: -122.4194 });

                // Update driver marker to restaurant position
                if (driverDriverMarker) {
                    driverDriverMarker.setLatLng([restaurantLoc.lat, restaurantLoc.lng]);
                }

                // Redraw route for "delivering" status (will trigger animation)
                await drawDriverDeliveryRoute(restaurantLoc, restaurantLoc, deliveryLoc, "delivering");
            }

            // Update UI to show delivery phase
            setTimeout(() => {
                const button = document.querySelector("#active-delivery-content button");
                console.log("🔧 [BUTTON UPDATE] Button found:", button ? "YES" : "NO");
                if (button) {
                    button.textContent = "✓ Delivered to Customer";
                    button.onclick = completeDelivery;
                    console.log("✅ [BUTTON UPDATE] Button text updated to 'Delivered to Customer'");
                } else {
                    console.error("❌ [BUTTON UPDATE] Button not found!");
                }
            }, 100);
        } else {
            const result = await response.json();
            showToast(result.error || "Failed to mark as picked up", "error");
        }
    } catch (error) {
        console.error("Error marking pickup:", error);
        showToast("Network error", "error");
    }
}

async function completeDelivery() {
    if (!currentOffer || !currentOffer.deliveryId) {
        showToast("Error: Delivery information not available", "error");
        return;
    }

    try {
        const apiBase = window.APP_CONFIG?.API_BASE_URL || "";
        const response = await fetch(
            `${apiBase}/deliveries/${currentOffer.deliveryId}/complete`,
            {
                method: "PATCH",
                headers: {
                    "Authorization": `Bearer ${authToken}`,
                },
            }
        );

        if (response.ok) {
            showToast("Delivery completed! Great job!", "success");

            // Update driver map to show completion
            if (currentOrderData && driverDeliveryMap) {
                const deliveryLoc = parseLocation(currentOrderData.delivery_address, { lat: 37.7749, lng: -122.4194 });

                // Stop animation and move driver to customer location
                stopDriverMapAnimation();

                if (driverDriverMarker) {
                    driverDriverMarker.setLatLng([deliveryLoc.lat, deliveryLoc.lng]);
                }

                // Remove route line
                if (driverRouteLine) {
                    driverDeliveryMap.removeLayer(driverRouteLine);
                    driverRouteLine = null;
                }
            }

            // Clear current offer
            currentOffer = null;
            setTimeout(() => {
                showPage("driver-dashboard");
                loadDriverDashboard();
            }, 1500);
        } else {
            const result = await response.json();
            showToast(result.error || "Failed to mark as completed", "error");
        }
    } catch (error) {
        console.error("Error marking completion:", error);
        showToast("Network error", "error");
    }
}

// ========================================
// Profile Functions
// ========================================

async function loadProfile() {
    if (!authToken || !currentUser) {
        showToast("Please log in first", "error");
        showPage("login");
        return;
    }

    // Pre-fill email
    document.getElementById("profile-email").value = currentUser.email || "";

    // For debugging: show user ID
    console.log("User ID (for driver assignment):", currentUser.sub);

    // Show/hide fields based on role
    const isDriver = currentUser.role === 'driver';
    document.getElementById("customer-fields").style.display = isDriver ? 'none' : 'block';
    document.getElementById("driver-fields").style.display = isDriver ? 'block' : 'none';

    // Update profile header
    const roleBadge = document.getElementById("profile-role-badge");
    const avatarIcon = document.getElementById("profile-avatar-icon");

    if (isDriver) {
        roleBadge.textContent = "Driver";
        roleBadge.style.background = "rgba(69, 123, 157, 0.9)";
        avatarIcon.textContent = "🚗";
    } else {
        roleBadge.textContent = "Customer";
        roleBadge.style.background = "rgba(255, 255, 255, 0.2)";
        avatarIcon.textContent = "👤";
    }

    try {
        if (isDriver) {
            // Load driver profile
            try {
                const data = await apiCall(`/drivers/${currentUser.sub}`);
                if (data) {
                    document.getElementById("profile-name").value = data.name || "";
                    document.getElementById("profile-phone").value = data.phone || "";
                    document.getElementById("profile-vehicle-type").value = data.vehicle_type || "car";
                    document.getElementById("profile-license-plate").value = data.license_plate || "";
                    document.getElementById("profile-license-number").value = data.license_number || "";

                    // Update header name
                    document.getElementById("profile-header-name").textContent = data.name || "Driver Profile";
                }
            } catch (driverErr) {
                // Driver profile doesn't exist yet, that's okay
                console.log("Driver profile not found, will be created on save");
                document.getElementById("profile-header-name").textContent = "Driver Profile";
            }
        } else {
            // Load customer profile
            const response = await apiCall(`/users/me`);
            if (response && response.user) {
                const data = response.user;
                document.getElementById("profile-name").value = data.full_name || data.name || "";
                document.getElementById("profile-phone").value = data.phone || "";
                document.getElementById("profile-address").value = data.address || "";

                // Update header name
                document.getElementById("profile-header-name").textContent = data.full_name || data.name || "My Profile";
            }
        }
    } catch (err) {
        console.error("Error loading profile:", err);
        // Continue anyway, user can fill in the form
        document.getElementById("profile-header-name").textContent = isDriver ? "Driver Profile" : "My Profile";
    }
}

async function saveProfile(e) {
    e.preventDefault();

    const name = document.getElementById("profile-name").value;
    const phone = document.getElementById("profile-phone").value;
    const isDriver = currentUser.role === 'driver';

    try {
        if (isDriver) {
            // Save driver profile
            const vehicleType = document.getElementById("profile-vehicle-type").value;
            const licensePlate = document.getElementById("profile-license-plate").value;
            const licenseNumber = document.getElementById("profile-license-number").value;

            // Try to get current GPS location
            let driverLocation = {
                lat: 37.7749,
                lng: -122.4194
            };

            if (navigator.geolocation) {
                try {
                    const position = await new Promise((resolve, reject) => {
                        navigator.geolocation.getCurrentPosition(resolve, reject, {
                            timeout: 5000,
                            enableHighAccuracy: false
                        });
                    });
                    driverLocation = {
                        lat: position.coords.latitude,
                        lng: position.coords.longitude
                    };
                    console.log("Using GPS location:", driverLocation);
                } catch (gpsErr) {
                    console.log("GPS not available, using default location:", gpsErr.message);
                }
            }

            const driverData = {
                name: name,
                phone: phone,
                vehicle_type: vehicleType,
                license_plate: licensePlate,
                license_number: licenseNumber,
                location: driverLocation,
                status: "available"
            };

            // Backend handles upsert automatically
            const result = await apiCall(`/drivers/${currentUser.sub}`, "PUT", driverData);

            if (result) {
                showToast("Driver profile saved successfully!", "success");
                currentUser.name = name;
                document.getElementById("user-name").textContent = name || currentUser.email;
                document.getElementById("profile-header-name").textContent = name || "Driver Profile";
                // Refresh dashboard to show updated status
                if (document.getElementById("page-driver-dashboard").classList.contains("active")) {
                    setTimeout(() => loadDriverDashboard(), 500);
                }
            }
        } else {
            // Save customer profile
            const address = document.getElementById("profile-address").value;

            // Geocode the address to get coordinates
            let location = null;
            if (address) {
                const geocodeUrl = `https://nominatim.openstreetmap.org/search?format=json&q=${encodeURIComponent(address)}`;
                const geocodeResponse = await fetch(geocodeUrl);
                const geocodeData = await geocodeResponse.json();

                if (geocodeData && geocodeData.length > 0) {
                    location = `${geocodeData[0].lat},${geocodeData[0].lon}`;
                    console.log("Geocoded address:", location);
                }
            }

            const updateData = {
                full_name: name,
                phone: phone,
                address: address,
                location: location
            };

            const result = await apiCall(`/users/${currentUser.sub}`, "PUT", updateData);

            if (result) {
                showToast("Profile saved successfully!", "success");
                currentUser.name = name;
                document.getElementById("user-name").textContent = name || currentUser.email;
                document.getElementById("profile-header-name").textContent = name || "My Profile";
            }
        }
    } catch (err) {
        console.error("Error saving profile:", err);
        showToast("Failed to save profile", "error");
    }
}

// init
document.addEventListener("DOMContentLoaded", () => {
    // Restore session from localStorage
    const savedAuthToken = localStorage.getItem("authToken");
    const savedUser = localStorage.getItem("currentUser");

    if (savedAuthToken && savedUser) {
        try {
            authToken = savedAuthToken;
            currentUser = JSON.parse(savedUser);

            // Update UI to logged-in state
            document.getElementById("nav-auth").classList.add("hidden");
            document.getElementById("nav-user").classList.remove("hidden");
            document.getElementById("user-name").textContent = currentUser?.name || currentUser?.email || "User";

            // Connect WebSocket
            connectWebSocket();

            // Show appropriate interface based on role
            if (currentUser?.role === 'driver') {
                showDriverInterface();
            } else {
                // Load cart for customers
                loadCartFromServer();
            }

            console.log("Session restored for:", currentUser?.email);
        } catch (e) {
            console.error("Failed to restore session:", e);
            // Clear invalid data
            localStorage.removeItem("authToken");
            localStorage.removeItem("currentUser");
        }
    }

    // Check URL hash to restore page
    const hash = window.location.hash.replace('#', '');
    if (hash && document.getElementById(`page-${hash}`)) {
        // Don't call showPage if we're already on that page (avoid redundant navigation)
        const currentActivePage = document.querySelector(".page.active");
        if (!currentActivePage || currentActivePage.id !== `page-${hash}`) {
            showPage(hash);

            // Special handling for tracking page - restore the tracked order
            if (hash === 'tracking') {
                const trackedOrderId = localStorage.getItem("currentTrackedOrderId");
                if (trackedOrderId && authToken) {
                    // Small delay to ensure page is shown first
                    setTimeout(() => trackOrder(trackedOrderId), 100);
                }
            }
        }
    } else if (!hash) {
        // No hash means home page - ensure home is active
        showPage('home');
    }

    loadPopularRestaurants();
});
