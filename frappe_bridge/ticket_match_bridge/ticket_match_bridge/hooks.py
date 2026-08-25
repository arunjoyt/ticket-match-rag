app_name = "ticket_match_bridge"
app_title = "Ticket Match Bridge"
app_publisher = "Ticket Match RAG"
app_description = "Serves Ticket Match RAG's Matches inside Helpdesk's own agent ticket view."
app_email = "arunjoyt@gmail.com"
app_license = "mit"

# Redirects Helpdesk's own stubbed get_recent_similar_tickets() (hardcodes
# similar_tickets = []) to this app's implementation, which calls Ticket
# Match RAG's cache-backed GET /tickets/{ticket_name}/matches -- see ADR 0006.
override_whitelisted_methods = {
    "helpdesk.helpdesk.doctype.hd_ticket.api.get_recent_similar_tickets": (
        "ticket_match_bridge.overrides.get_recent_similar_tickets"
    ),
}
