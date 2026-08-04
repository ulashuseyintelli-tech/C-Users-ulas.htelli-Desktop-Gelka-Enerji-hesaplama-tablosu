"""widen reference doc dedup to include document_type

Revision ID: dc8343278cfa
Revises: a93beeaddf82
Create Date: 2026-08-04 23:47:44.206041

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = 'dc8343278cfa'
down_revision: Union[str, None] = 'a93beeaddf82'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """
    uq_reference_doc_dedup constraint'ini (tenant_id, customer_id, sha256) →
    (tenant_id, customer_id, sha256, document_type) olarak genişletir.

    Additive/backward-compatible: mevcut veriler etkilenmez (constraint yalnız
    YENİ INSERT'lerde kontrol edilir). Kök neden (LOCAL UAT FINAL
    REMEDIATION): aynı dosya YANLIŞ document_type ile yüklendiğinde, dedup
    sha256'ya bakıyordu — kullanıcı SONRA aynı dosyayı DOĞRU türle tekrar
    yüklediğinde, sistem mevcut (yanlış türdeki) kaydı sessizce döndürüyordu,
    DB'ye manuel müdahale olmadan düzeltilemiyordu. Artık aynı dosya farklı
    document_type ile yüklenirse YENİ, ayrı bir kayıt oluşur — önceki
    (yanlış) kayıt ve onun extraction geçmişi DEĞİŞTİRİLMEDEN kalır.
    """
    with op.batch_alter_table('uploaded_reference_documents', schema=None, recreate='always') as batch_op:
        batch_op.drop_constraint('uq_reference_doc_dedup', type_='unique')
        batch_op.create_unique_constraint(
            'uq_reference_doc_dedup',
            ['tenant_id', 'customer_id', 'sha256', 'document_type'],
        )


def downgrade() -> None:
    """
    Rollback: constraint'i (tenant_id, customer_id, sha256) haline geri döndürür.

    DİKKAT: upgrade sonrası aynı (tenant_id, customer_id, sha256) için birden
    fazla document_type'lı satır oluşmuşsa, bu downgrade IntegrityError ile
    BAŞARISIZ olur — bu KASITLIDIR (veri kaybını/sessiz satır silmeyi önler).
    """
    with op.batch_alter_table('uploaded_reference_documents', schema=None, recreate='always') as batch_op:
        batch_op.drop_constraint('uq_reference_doc_dedup', type_='unique')
        batch_op.create_unique_constraint(
            'uq_reference_doc_dedup',
            ['tenant_id', 'customer_id', 'sha256'],
        )
