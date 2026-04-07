// ── Configuration ──
const API_BASE = window.APP_CONFIG?.API_BASE_URL || "";
const WS_URL = window.APP_CONFIG?.WEBSOCKET_URL || "";


let currentUser = null;
let authToken = null;
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
let currentOrderData = null;


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
            const data = JSON.parse(event.data);
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
        default:
            console.log("Unknown WebSocket message type:", messageType, data);
    }
}

function updateOrderStatus(data) {
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

    // Update tracking timeline if on tracking page
    updateTrackingTimeline(data.order_id, data.status);

    // Show notification
    const statusMessages = {
        "confirmed": "Your order has been confirmed!",
        "preparing": "Restaurant is preparing your food",
        "delivering": "Driver is on the way!",
        "completed": "Order delivered! Enjoy your meal!",
        "delivered": "Order delivered! Enjoy your meal!",
        "cancelled": "Order has been cancelled"
    };

    const message = statusMessages[status] || `Order status: ${data.status}`;
    showToast(message, status === "cancelled" ? "error" : "success");
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
    if (driverLoc) {
        updateDriverMarkerOnMap(driverLoc.lat, driverLoc.lng);

        // Draw route: Restaurant -> Driver -> Destination
        drawRoute(restaurantLoc, driverLoc, deliveryLoc);
    } else {
        // Draw direct line from restaurant to destination (before driver assigned)
        drawRoute(restaurantLoc, null, deliveryLoc);
    }

    // Fit bounds to show all markers
    fitMapBounds(restaurantLoc, deliveryLoc, driverLoc, orderData.status);
}

function parseLocation(locationData, fallback = null) {
    if (!locationData) return fallback;

    // Handle different formats: {lat, lng}, "lat,lng", {latitude, longitude}
    if (typeof locationData === 'string') {
        const parts = locationData.split(',');
        if (parts.length === 2) {
            return { lat: parseFloat(parts[0]), lng: parseFloat(parts[1]) };
        }
    } else if (typeof locationData === 'object') {
        if (locationData.lat !== undefined && locationData.lng !== undefined) {
            return { lat: parseFloat(locationData.lat), lng: parseFloat(locationData.lng) };
        } else if (locationData.latitude !== undefined && locationData.longitude !== undefined) {
            return { lat: parseFloat(locationData.latitude), lng: parseFloat(locationData.longitude) };
        }
    }

    return fallback;
}

function drawRoute(restaurantLoc, driverLoc, deliveryLoc) {
    // Remove existing route line
    if (routeLine) {
        trackingMap.removeLayer(routeLine);
    }

    const points = [];

    if (driverLoc) {
        // Driver is on the way: Restaurant -> Driver -> Destination
        points.push([restaurantLoc.lat, restaurantLoc.lng]);
        points.push([driverLoc.lat, driverLoc.lng]);
        points.push([deliveryLoc.lat, deliveryLoc.lng]);
    } else {
        // No driver yet: Restaurant -> Destination
        points.push([restaurantLoc.lat, restaurantLoc.lng]);
        points.push([deliveryLoc.lat, deliveryLoc.lng]);
    }

    // Draw polyline
    routeLine = L.polyline(points, {
        color: '#e63946',
        weight: 4,
        opacity: 0.7,
        dashArray: driverLoc ? null : '10, 10',  // Dashed when no driver
    }).addTo(trackingMap);
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
    } else if (status === "DELIVERING" && order.delivery_id) {
        return `
            <div style="background: linear-gradient(135deg, #cce5ff 0%, #b8daff 100%); padding: 1.5rem; border-radius: 8px; border-left: 4px solid #004085;">
                <h4 style="margin: 0 0 0.5rem 0; font-size: 1rem; color: #004085;">🚗 On the Way!</h4>
                <p style="color: #004085; margin: 0.25rem 0 0.75rem 0; font-size: 0.9rem;">
                    <strong>Delivery ID:</strong> ${order.delivery_id.substring(0, 8)}
                </p>
                <div id="tracking-eta" style="font-size: 1.1rem; font-weight: 600; color: #004085; margin-bottom: 0.5rem;"></div>
                <div id="driver-location"></div>
            </div>
        `;
    } else if (order.delivery_id && (status === "CONFIRMED" || status === "PLACED" || status === "PENDING")) {
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
            // TODO: fetch user info from token or API
            currentUser = { email };
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

    // Load cart from backend
    await loadCartFromServer();

    // Connect WebSocket for real-time updates
    connectWebSocket();

    showToast("Welcome back!", "success");
    showPage("home");
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
    const info = document.getElementById("tracking-info");
    const timeline = document.getElementById("tracking-status");

    // Clean up previous map
    if (trackingMap) {
        trackingMap.remove();
        trackingMap = null;
        driverMarker = null;
        restaurantMarker = null;
        destinationMarker = null;
        routeLine = null;
        currentOrderData = null;
    }
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

                ${getStatusBasedInfo(order)}
            </div>
        `;

        // Render timeline based on current status
        updateTrackingTimeline(orderId, order.status);

        // Initialize map for all orders (not just with delivery_id)
        // This shows restaurant and destination even before driver is assigned
        const mapData = {
            restaurant_location: order.restaurant_location || "37.7749,-122.4194", // SF default
            delivery_address: order.delivery_address || "37.7849,-122.4094", // SF default
            driver_location: null,
            status: order.status
        };
        initializeTrackingMap(mapData);

        // Subscribe to WebSocket updates for this order
        if (order.delivery_id) {
            // Use the short form of delivery_id (first 8 chars) to match simulator format
            const deliveryId = order.delivery_id.substring(0, 8);
            console.log(`Auto-subscribing to delivery: ${deliveryId} (full: ${order.delivery_id})`);
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
            key: "DELIVERING",
            label: "Out for Delivery",
            description: "Driver is on the way",
            icon: "🚗"
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
    } else if (normalizedStatus === "DELIVERING") {
        activeStepIndex = 3;
    } else if (normalizedStatus === "COMPLETED" || normalizedStatus === "DELIVERED") {
        activeStepIndex = 4;
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

// init
document.addEventListener("DOMContentLoaded", () => {
    loadPopularRestaurants();
});
