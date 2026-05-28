// MongoDB runs every *.js in /docker-entrypoint-initdb.d once, on first init.
// Seeds a small "ignition" database with one demo collection so the store is
// non-empty when you first connect (replace with your own schema).
db = db.getSiblingDB("ignition");
db.createCollection("readings");
db.readings.insertOne({
  source: "ignition-stack-seed",
  note: "Demo document. Safe to delete.",
  createdAt: new Date(),
});
