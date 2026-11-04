"""The Chef: the built-in recipe library, planner and shopping list.

Everything here lives in the same SQLite file as the rest of the app, in its
own tables, and is shared by every user on the server. Only favorites,
ratings, private notes and cook history are per user.
"""
