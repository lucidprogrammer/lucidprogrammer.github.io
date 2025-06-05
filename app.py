import argparse
import os
import secrets
from datetime import timedelta
import logging
import redis
from flask import Flask, redirect, url_for, session, render_template, request
from authlib.integrations.flask_client import OAuth
from authlib.common.errors import AuthlibBaseError
from flask_session import Session # Import Session for Redis

# --- Configuration ---
# Flask app setup
app = Flask(__name__)
app.secret_key = secrets.token_urlsafe(32)

# OIDC Configuration
KEYCLOAK_SERVER_URL = 'http://localhost:8080'
KEYCLOAK_REALM = 'enterprise-sso'
KEYCLOAK_CLIENT_ID = 'admin-dashboard'

# OAuth initialization
oauth = OAuth(app)
keycloak_client = None  # Will be initialized in main()

# Global logout tracking (use Redis in production)
# logged_out_users = set() # Original in-memory version
# --- Production Redis for Global Logout State ---
logout_redis = redis.from_url('redis://localhost:6379', db=1)

# Portal type and info, will be set in main()
PORTAL_TYPE = None
PORTAL_INFO = {}
STATIC_DIR = None

# --- Logging Setup ---
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s %(levelname)s %(name)s %(message)s'
)
logger = logging.getLogger(__name__)

# --- Production Session Configuration (Redis) ---
app.config.update(
    SESSION_COOKIE_SECURE=True,  # HTTPS only
    SESSION_COOKIE_HTTPONLY=True,  # No JavaScript access
    SESSION_COOKIE_SAMESITE='Lax',  # CSRF protection
    PERMANENT_SESSION_LIFETIME=timedelta(hours=8)  # Session timeout
)
app.config['SESSION_TYPE'] = 'redis'
app.config['SESSION_REDIS'] = redis.from_url('redis://localhost:6379')
app.config['SESSION_PERMANENT'] = False
app.config['SESSION_USE_SIGNER'] = True
app.config['SESSION_KEY_PREFIX'] = 'admin:' # Changed from 'session:' to 'admin:' as per doc

Session(app)


# --- Helper Functions ---
def parse_arguments():
    parser = argparse.ArgumentParser(description='Flask SSO Demo Portal Server')
    parser.add_argument('--portal',
                      choices=['internal', 'external', 'admin'],
                      default='internal',
                      help='Which portal to serve')
    parser.add_argument('--port',
                      type=int,
                      default=5000,
                      help='Port to run the server on')
    return parser.parse_args()

def get_portal_info(portal_type):
    # This function would typically load from a config file or database
    if portal_type == 'admin':
        return {
            'name': 'Admin Dashboard',
            'icon': '👑',
            'description': 'Centralized administration and SSO management interface.'
        }
    # Add other portal types if needed
    return {
        'name': 'Default Portal',
        'icon': '🌐',
        'description': 'A generic portal.'
    }

def get_static_directory(portal_type):
    if portal_type == 'admin':
        return 'admin-dashboard'
    # Define for other portal types if they exist
    return 'static'

def setup_oidc_client():
    global keycloak_client # Ensure we're modifying the global client
    keycloak_client = oauth.register(
        'keycloak',
        client_id=KEYCLOAK_CLIENT_ID,
        client_secret=None,  # Public client
        authorize_url=f'{KEYCLOAK_SERVER_URL}/realms/{KEYCLOAK_REALM}/protocol/openid-connect/auth',
        access_token_url=f'{KEYCLOAK_SERVER_URL}/realms/{KEYCLOAK_REALM}/protocol/openid-connect/token',
        userinfo_endpoint=f'{KEYCLOAK_SERVER_URL}/realms/{KEYCLOAK_REALM}/protocol/openid-connect/userinfo',
        jwks_uri=f'{KEYCLOAK_SERVER_URL}/realms/{KEYCLOAK_REALM}/protocol/openid-connect/certs',
        client_kwargs={
            'scope': 'openid profile email'
        }
    )
    return keycloak_client


# --- Global Logout State Management with Redis ---
def is_user_logged_out(user_id):
    return logout_redis.exists(f"logout:{user_id}") or logout_redis.exists("logout:*")

def mark_user_logged_out(user_id, ttl=3600):
    logout_redis.setex(f"logout:{user_id}", ttl, "1")

def clear_logout_state(user_id):
    logout_redis.delete(f"logout:{user_id}")
    # Removed logout_redis.delete("logout:*") as per original markdown,
    # it should only be cleared on login for that user, or by admin action.
    # Global logout '*' is handled differently.

# --- Authentication Middleware ---
def require_auth(f):
    def decorated_function(*args, **kwargs):
        logger.info(f"🔍 require_auth check: session keys = {list(session.keys())}")

        if 'user' not in session:
            logger.info(f"🔒 No user in session, redirecting to login")
            return redirect(url_for('admin_login'))

        user_id = session.get('user', {}).get('id')
        # Using Redis based check
        if is_user_logged_out(user_id):
            logger.info(f"🔒 User {user_id} has been logged out globally, clearing session")
            # Store current user_id before clearing session for potential later use
            original_user_id = session.get('user', {}).get('id')
            session.clear()
            # Optionally, mark this specific user as logged out if a global '*' caused it
            if logout_redis.exists("logout:*") and original_user_id:
                 mark_user_logged_out(original_user_id) # Mark specific user too
            return redirect(url_for('admin_login'))

        logger.info(f"✅ User authenticated: {session.get('user', {}).get('username', 'unknown')}")
        return f(*args, **kwargs)

    decorated_function.__name__ = f.__name__
    return decorated_function

# --- Request Hooks for Logging ---
@app.before_request
def log_request_info():
    logger.info(f"Request: {request.method} {request.url}")
    if 'user' in session and PORTAL_TYPE == 'admin': # Log user only for admin portal context
        logger.info(f"User: {session['user']['username']}")

@app.after_request
def log_response_info(response):
    logger.info(f"Response: {response.status_code}")
    return response

# --- Routes ---
@app.route('/admin/login')
def admin_login():
    if PORTAL_TYPE != 'admin':
        return "Not available on this portal", 404

    if 'user' in session:
        # If user is already in session, check if they were globally logged out
        user_id = session.get('user', {}).get('id')
        if is_user_logged_out(user_id):
            logger.info(f"🔒 User {user_id} in session but globally logged out, clearing session")
            session.clear()
            # Render login page instead of redirecting to dashboard
            return render_template('login.html', portal_info=PORTAL_INFO)
        return redirect(url_for('admin_dashboard'))

    return render_template('login.html', portal_info=PORTAL_INFO)

@app.route('/admin/auth')
def admin_auth():
    if PORTAL_TYPE != 'admin':
        return "Not available on this portal", 404
    if not keycloak_client: # Ensure client is initialized
        logger.error("Keycloak client not initialized!")
        return render_template('error.html', error="OIDC client not configured.", portal_info=PORTAL_INFO)

    redirect_uri = url_for('admin_callback', _external=True)
    return keycloak_client.authorize_redirect(redirect_uri)

@app.route('/admin/callback')
def admin_callback():
    if PORTAL_TYPE != 'admin':
        return "Not available on this portal", 404
    if not keycloak_client:
        logger.error("Keycloak client not initialized!")
        return render_template('error.html', error="OIDC client not configured.", portal_info=PORTAL_INFO)

    try:
        token = keycloak_client.authorize_access_token()
        user_info = token.get('userinfo')

        if user_info:
            session['user'] = {
                'id': user_info.get('sub'),
                'username': user_info.get('preferred_username'),
                'email': user_info.get('email'),
                'name': user_info.get('name', user_info.get('preferred_username')),
                'roles': user_info.get('realm_access', {}).get('roles', [])
            }
            session['access_token'] = token.get('access_token')
            logger.info(f"✅ Admin user logged in: {session['user']['username']}")

            user_id = session['user']['id']
            # Clear specific user logout and global '*' logout on successful login
            clear_logout_state(user_id) # Clears user specific and '*'
            logout_redis.delete("logout:*") # Explicitly clear global logout flag

            return redirect(url_for('admin_dashboard'))
        else:
            return render_template('error.html',
                                 error="Authentication failed - no user information received",
                                 portal_info=PORTAL_INFO)

    except AuthlibBaseError as e:
        logger.error(f"OIDC authentication error: {e}")
        return render_template('error.html',
                             error=f"Authentication failed: {str(e)}",
                             portal_info=PORTAL_INFO)

@app.route('/admin/dashboard')
@require_auth
def admin_dashboard():
    if PORTAL_TYPE != 'admin':
        return "Not available on this portal", 404

    user = session.get('user')
    # Ensure roles is a list
    user_roles = user.get('roles', []) if isinstance(user.get('roles'), list) else []
    is_admin = 'admin' in user_roles or 'approver' in user_roles

    # Mock data for demo
    stats = {
        'pending_approvals': 23,
        'active_users': 156,
        'connected_systems': 8,
        'system_uptime': '99.8%'
    }
    pending_items = [
        {'type': 'Leave Request', 'description': 'John Doe - Annual Leave (3 days)'},
        {'type': 'Purchase Order', 'description': 'IT Equipment - ฿125,000'},
        {'type': 'Vendor Registration', 'description': 'ABC Consulting Co.'},
        {'type': 'Project Budget', 'description': 'Digital Transformation Phase 2'}
    ]

    return render_template('dashboard.html',
                         user=user,
                         is_admin=is_admin,
                         stats=stats,
                         pending_items=pending_items,
                         portal_info=PORTAL_INFO)

# --- Enterprise Logout Implementation ---
@app.route('/logout.html', methods=['GET', 'POST'])
@app.route('/logout', methods=['GET', 'POST']) # Added /logout as per markdown
def logout():
    # This route handles both front-channel and back-channel logout
    # It's not portal-specific in the route itself, but logic might differ

    if request.method == 'POST':
        # Back-channel logout from Keycloak
        logger.info(f"🔴 BACK-CHANNEL LOGOUT received from Keycloak at {PORTAL_INFO.get('name', 'N/A')}")

        logout_token = request.form.get('logout_token')
        if logout_token:
            logger.info(f"📝 Logout token received: {logout_token[:50]}...")
            # Here you would typically validate the logout token
            # For demo, we assume it's valid and proceed

        # The markdown implies that a back-channel logout might not be tied to a current session.
        # It's a global instruction from Keycloak.
        # It might provide a user SID or sub in the token to identify who to log out.
        # For this demo, it uses a global '*' or logs out current session user if any.

        # Based on markdown: "For demo, also add wildcard to logout all current users"
        # And "Add current session user to logged out users"

        current_user_id_in_session = session.get('user', {}).get('id')
        if current_user_id_in_session:
            mark_user_logged_out(current_user_id_in_session)
            logger.info(f"🗑️ Marked user {current_user_id_in_session} from session for global logout via back-channel")

        # Mark all users for logout (wildcard)
        mark_user_logged_out('*') # This will set 'logout:*' in Redis
        logger.info("🗑️ Marked all users (*) for global logout via back-channel")

        # Clear the current session if it exists, as this instance received the call
        if 'user' in session:
             logger.info(f"✅ Clearing session for {session.get('user',{}).get('username','unknown')} due to back-channel logout")
             session.clear()

        return "Logout acknowledged", 200

    else:  # GET request - front-channel logout (user initiated from Keycloak or other app)
        logger.info(f"🟡 FRONT-CHANNEL LOGOUT received at {PORTAL_INFO.get('name', 'N/A')}")

        current_user_id = session.get('user', {}).get('id')
        if current_user_id:
            mark_user_logged_out(current_user_id) # Mark this specific user as logged out
            logger.info(f"🗑️ Clearing admin session and marking user {current_user_id} as logged out via front-channel")
        else:
            logger.info("🗑️ Clearing admin session via front-channel (no user ID in session)")

        session.clear()

        # For front-channel, redirect to a local login page.
        # The specific login page might depend on which portal this instance is serving.
        if PORTAL_TYPE == 'admin':
            return redirect(url_for('admin_login'))
        else:
            # Generic fallback if not admin portal or no specific portal logic here
            return "Logged out. Please login again.", 200


@app.route('/admin/logout')
def admin_logout(): # User clicks logout button in this app
    if PORTAL_TYPE != 'admin':
        return "Not available on this portal", 404

    user_id = session.get('user', {}).get('id')
    username = session.get('user', {}).get('username', 'unknown')

    if user_id:
        logger.info(f"🔴 Admin user logout initiated for: {username} ({user_id})")
        mark_user_logged_out(user_id) # Mark this specific user as logged out
    else:
        logger.info(f"🔴 Admin logout initiated, but no user in session.")

    # Clear local session
    session.clear()

    # Redirect to Keycloak logout endpoint
    # This ensures global SSO session is terminated
    post_logout_redirect_uri = url_for('admin_login', _external=True)
    logout_url = f"{KEYCLOAK_SERVER_URL}/realms/{KEYCLOAK_REALM}/protocol/openid-connect/logout?" \
                 f"post_logout_redirect_uri={post_logout_redirect_uri}"

    return redirect(logout_url)


# --- Main Application Runner ---
def main():
    global PORTAL_TYPE, PORTAL_INFO, STATIC_DIR, keycloak_client, app

    args = parse_arguments()
    PORTAL_TYPE = args.portal
    PORTAL_INFO = get_portal_info(PORTAL_TYPE)
    # STATIC_DIR is used to determine template_folder and static_folder paths
    # but Flask by default uses 'static' and 'templates' folders in the app root
    # For the admin portal, we need to set these specifically if they are nested.

    current_app_path = os.path.dirname(os.path.abspath(__file__))

    if PORTAL_TYPE == 'admin':
        # Initialize OIDC client only for admin portal
        keycloak_client = setup_oidc_client()

        # Set Flask template and static folder for admin portal
        # Assuming 'admin-dashboard' is at the same level as app.py
        # and contains 'templates' and 'static' subdirectories.
        admin_template_folder = os.path.join(current_app_path, 'admin-dashboard', 'templates')
        admin_static_folder = os.path.join(current_app_path, 'admin-dashboard', 'static')

        app.template_folder = admin_template_folder
        app.static_folder = admin_static_folder
        logger.info(f"🛠️ Admin portal configured: Templates at {admin_template_folder}, Static at {admin_static_folder}")

    else:
        # Configure for other portal types if necessary
        # For now, they might use default 'templates' and 'static' folders
        # or share with admin if structure is different.
        # This part needs clarification if other portals have distinct UI.
        # Defaulting to standard flask locations if not admin.
        app.template_folder = os.path.join(current_app_path, 'templates')
        app.static_folder = os.path.join(current_app_path, 'static')
        logger.info(f"🛠️ Non-admin portal ({PORTAL_TYPE}) configured: Templates at {app.template_folder}, Static at {app.static_folder}")


    logger.info(f"🚀 Starting {PORTAL_INFO['icon']} {PORTAL_INFO['name']} on port {args.port}")
    app.run(host='0.0.0.0', port=args.port, debug=False) # debug=False for production/demonstration

if __name__ == '__main__':
    main()
