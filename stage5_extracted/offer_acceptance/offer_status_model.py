from enum import Enum

class OfferStatus(str, Enum):
    DRAFT='DRAFT'
    SENT='SENT'
    ACCEPTED='ACCEPTED'
    REJECTED='REJECTED'
    CONTRACTING='CONTRACTING'
    CLOSED='CLOSED'
