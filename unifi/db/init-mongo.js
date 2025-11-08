db.getSiblingDB("unifi").createUser({
  user: "unifi",
  pwd: "unifi",
  roles: [
    { role: "dbOwner", db: "unifi" }
  ]
});
