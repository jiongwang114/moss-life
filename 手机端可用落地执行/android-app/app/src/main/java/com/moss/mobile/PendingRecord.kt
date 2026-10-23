package com.moss.mobile

import androidx.room.Dao
import androidx.room.Database
import androidx.room.Entity
import androidx.room.Insert
import androidx.room.OnConflictStrategy
import androidx.room.Query
import androidx.room.RoomDatabase
import androidx.room.migration.Migration
import androidx.sqlite.db.SupportSQLiteDatabase
import kotlinx.coroutines.flow.Flow

@Entity(tableName = "pending_records")
data class PendingRecord(
    @androidx.room.PrimaryKey val id: String,
    val rawInput: String,
    val status: String,
    val updatedAt: Long,
    val firstSentAt: String,
    val offsetMinutes: Int,
    val recordDate: String
)

@Dao
interface PendingRecordDao {
    @Query("SELECT * FROM pending_records ORDER BY updatedAt DESC")
    fun observeAll(): Flow<List<PendingRecord>>

    @Query("SELECT * FROM pending_records ORDER BY updatedAt DESC LIMIT 1")
    suspend fun latest(): PendingRecord?

    @Insert(onConflict = OnConflictStrategy.REPLACE)
    suspend fun save(record: PendingRecord)

    @Query("DELETE FROM pending_records WHERE id = :id")
    suspend fun delete(id: String)
}

@Entity(tableName = "web_state")
data class WebState(@androidx.room.PrimaryKey val key: String, val value: String)

@Dao
interface WebStateDao {
    @Query("SELECT value FROM web_state WHERE `key` = :key")
    suspend fun get(key: String): String?
    @Insert(onConflict = OnConflictStrategy.REPLACE)
    suspend fun put(state: WebState)
}

@Database(entities = [PendingRecord::class, WebState::class], version = 3, exportSchema = false)
abstract class MossDatabase : RoomDatabase() {
    abstract fun pendingRecordDao(): PendingRecordDao
    abstract fun webStateDao(): WebStateDao
}

val WEB_STATE_MIGRATION_2_3 = object : Migration(2, 3) {
    override fun migrate(database: SupportSQLiteDatabase) {
        database.execSQL("CREATE TABLE IF NOT EXISTS web_state (`key` TEXT NOT NULL, `value` TEXT NOT NULL, PRIMARY KEY(`key`))")
    }
}

val PENDING_RECORD_MIGRATION_1_2 = object : Migration(1, 2) {
    override fun migrate(database: SupportSQLiteDatabase) {
        database.execSQL("ALTER TABLE pending_records ADD COLUMN firstSentAt TEXT NOT NULL DEFAULT ''")
        database.execSQL("ALTER TABLE pending_records ADD COLUMN offsetMinutes INTEGER NOT NULL DEFAULT 0")
        database.execSQL("ALTER TABLE pending_records ADD COLUMN recordDate TEXT NOT NULL DEFAULT ''")
    }
}

