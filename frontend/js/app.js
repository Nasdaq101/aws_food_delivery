// ── Configuration ──
const API_BASE = window.APP_CONFIG?.API_BASE_URL || "";


let currentUser = null;
let authToken = null;
let cart = { items: [] }; // Removed restaurant_id - now supports multiple restaurants
let pendingVerificationEmail = null;
let previousPage = "home"; // Track where we came from


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
        container.innerHTML = orders.length
            ? orders.map(renderOrderCard).join("")
            : '<div class="empty-state"><p>No orders yet</p></div>';
    } catch (err) {
        console.error("Failed to load orders:", err);
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
                <strong>$${parseFloat(order.total || 0).toFixed(2)}</strong>
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
