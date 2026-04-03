// ── Configuration ──
const API_BASE = ""; // TODO


let currentUser = null;
let authToken = null;
let cart = { items: [], restaurant_id: null };


function showPage(pageId) {
    document.querySelectorAll(".page").forEach((p) => p.classList.remove("active"));
    const page = document.getElementById(`page-${pageId}`);
    if (page) page.classList.add("active");

    if (pageId === "restaurants") loadRestaurants();
    if (pageId === "orders") loadOrders();
    if (pageId === "cart") renderCart();
}


// ── API Helpers ──
async function apiCall(endpoint, method = "GET", body = null) {
    const headers = { "Content-Type": "application/json" };
    if (authToken) headers["Authorization"] = `Bearer ${authToken}`;

    const opts = { method, headers };
    if (body) opts.body = JSON.stringify(body);

    const res = await fetch(`${API_BASE}${endpoint}`, opts);
    return res.json();
}


async function handleLogin(e) {
    e.preventDefault();
    const email = document.getElementById("login-email").value;
    const password = document.getElementById("login-password").value;

    try {
        const data = await apiCall("/auth/login", "POST", { email, password });
        if (data.token) {
            authToken = data.token;
            currentUser = data.user;
            onLoginSuccess();
        } else {
            showToast(data.message || "Login failed", "error");
        }
    } catch {
        showToast("Login failed - API not connected", "error");
    }
}

async function handleSignup(e) {
    e.preventDefault();
    const name = document.getElementById("signup-name").value;
    const email = document.getElementById("signup-email").value;
    const password = document.getElementById("signup-password").value;
    const role = document.getElementById("signup-role").value;

    try {
        const data = await apiCall("/auth/signup", "POST", { name, email, password, role });
        if (data.user_id) {
            showToast("Account created! Please login.", "success");
            showPage("login");
        } else {
            showToast(data.message || "Signup failed", "error");
        }
    } catch {
        showToast("Signup failed - API not connected", "error");
    }
}

function onLoginSuccess() {
    document.getElementById("nav-auth").classList.add("hidden");
    document.getElementById("nav-user").classList.remove("hidden");
    document.getElementById("user-name").textContent = currentUser?.name || "User";
    showToast("Welcome back!", "success");
    showPage("home");
}

function logout() {
    currentUser = null;
    authToken = null;
    document.getElementById("nav-auth").classList.remove("hidden");
    document.getElementById("nav-user").classList.add("hidden");
    showPage("home");
}


async function loadRestaurants() {
    const container = document.getElementById("restaurants-list");
    try {
        const data = await apiCall("/restaurants");
        const restaurants = data.restaurants || data || [];
        container.innerHTML = restaurants.map(renderRestaurantCard).join("");
    } catch {
        container.innerHTML = getMockRestaurants().map(renderRestaurantCard).join("");
    }
}


async function loadPopularRestaurants() {
    const container = document.getElementById("popular-restaurants");
    try {
        const data = await apiCall("/restaurants");
        const restaurants = (data.restaurants || data || []).slice(0, 6);
        container.innerHTML = restaurants.map(renderRestaurantCard).join("");
    } catch {
        container.innerHTML = getMockRestaurants().slice(0, 6).map(renderRestaurantCard).join("");
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
    showPage("restaurant-detail");
    const header = document.getElementById("restaurant-header");
    const menuList = document.getElementById("menu-list");

    try {
        const restaurant = await apiCall(`/restaurants/${id}`);
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
    } catch {
        const mock = getMockMenu();
        header.innerHTML = `<h2>Sample Restaurant</h2><div class="meta"><span>Various</span><span class="rating">★ 4.5</span></div>`;
        menuList.innerHTML = mock.map((item) => renderMenuItem(item, id)).join("");
    }
}


function renderMenuItem(item, restaurantId) {
    return `
        <div class="menu-item">
            <div class="menu-item-info">
                <h4>${item.name}</h4>
                <p>${item.description || ''}</p>
            </div>
            <div class="menu-item-actions">
                <span class="menu-item-price">$${(item.price || 0).toFixed(2)}</span>
                <button class="btn btn-primary btn-small" onclick="addToCart('${restaurantId}', '${item.item_id}', '${item.name}', ${item.price})">Add</button>
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


function addToCart(restaurantId, itemId, name, price) {
    if (cart.restaurant_id && cart.restaurant_id !== restaurantId) {
        if (!confirm("Adding items from a different restaurant will clear your current cart. Continue?")) return;
        cart.items = [];
    }
    cart.restaurant_id = restaurantId;

    const existing = cart.items.find((i) => i.item_id === itemId);
    if (existing) {
        existing.quantity += 1;
    } else {
        cart.items.push({ item_id: itemId, name, price, quantity: 1 });
    }

    updateCartCount();
    showToast(`${name} added to cart`, "success");
}


function updateCartCount() {
    const count = cart.items.reduce((sum, i) => sum + i.quantity, 0);
    document.getElementById("cart-count").textContent = count;
}


function updateQuantity(itemId, delta) {
    const item = cart.items.find((i) => i.item_id === itemId);
    if (!item) return;
    item.quantity += delta;
    if (item.quantity <= 0) {
        cart.items = cart.items.filter((i) => i.item_id !== itemId);
    }
    if (cart.items.length === 0) cart.restaurant_id = null;
    updateCartCount();
    renderCart();
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

    container.innerHTML = cart.items
        .map(
            (item) => `
        <div class="cart-item">
            <div class="cart-item-info">
                <strong>${item.name}</strong>
                <div>$${item.price.toFixed(2)} each</div>
            </div>
            <div class="cart-item-qty">
                <button onclick="updateQuantity('${item.item_id}', -1)">−</button>
                <span>${item.quantity}</span>
                <button onclick="updateQuantity('${item.item_id}', 1)">+</button>
            </div>
            <strong>$${(item.price * item.quantity).toFixed(2)}</strong>
        </div>
    `
        )
        .join("");

    const total = cart.items.reduce((sum, i) => sum + i.price * i.quantity, 0);
    document.getElementById("cart-total-price").textContent = `$${total.toFixed(2)}`;
}


async function placeOrder() {
    if (!authToken) {
        showToast("Please login to place an order", "error");
        showPage("login");
        return;
    }

    const promoCode = document.getElementById("promo-code").value;
    try {
        const data = await apiCall("/orders", "POST", {
            restaurant_id: cart.restaurant_id,
            items: cart.items,
            promo_code: promoCode || undefined,
        });
        if (data.order_id) {
            cart = { items: [], restaurant_id: null };
            updateCartCount();
            showToast("Order placed successfully!", "success");
            showPage("orders");
        } else {
            showToast(data.message || "Order failed", "error");
        }
    } catch {
        showToast("Order failed - API not connected", "error");
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
        container.innerHTML = orders.length
            ? orders.map(renderOrderCard).join("")
            : '<div class="empty-state"><p>No orders yet</p></div>';
    } catch {
        container.innerHTML = '<div class="empty-state"><p>Could not load orders</p></div>';
    }
}


function renderOrderCard(order) {
    const statusClass = `status-${order.status || "pending"}`;
    return `
        <div class="order-card">
            <div class="order-header">
                <div>
                    <strong>Order #${(order.order_id || "").substring(0, 8)}</strong>
                    <div style="color: var(--text-light); font-size: 0.85rem">${order.created_at || ''}</div>
                </div>
                <span class="order-status ${statusClass}">${(order.status || "pending").toUpperCase()}</span>
            </div>
            <div>
                ${(order.items || []).map((i) => `<span>${i.name} x${i.quantity}</span>`).join(", ")}
            </div>
            <div style="display: flex; justify-content: space-between; margin-top: 0.75rem">
                <strong>$${(order.total || 0).toFixed(2)}</strong>
                ${order.status === "delivering" ? `<button class="btn btn-primary btn-small" onclick="trackOrder('${order.order_id}')">Track</button>` : ""}
            </div>
        </div>
    `;
}


function trackOrder(orderId) {
    showPage("tracking");
    const info = document.getElementById("tracking-info");
    const timeline = document.getElementById("tracking-status");

    info.innerHTML = `<h3>Order #${orderId.substring(0, 8)}</h3>`;

    const steps = [
        { label: "Order Placed", status: "completed" },
        { label: "Restaurant Confirmed", status: "completed" },
        { label: "Preparing Food", status: "completed" },
        { label: "Driver Picked Up", status: "active" },
        { label: "On the Way", status: "" },
        { label: "Delivered", status: "" },
    ];

    timeline.innerHTML = steps
        .map(
            (s) => `
        <div class="tracking-step ${s.status}">
            <strong>${s.label}</strong>
        </div>
    `
        )
        .join("");

    // TODO: connect WebSocket for real-time updates
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
